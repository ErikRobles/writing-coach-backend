from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from groq import AsyncGroq
import bcrypt
import jwt
from datetime import datetime, timedelta
import os
import json
import uuid
from dotenv import load_dotenv

load_dotenv()

from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5000", "https://localhost:3000", "*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB Setup
MONGODB_URL = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
motor_client = AsyncIOMotorClient(MONGODB_URL)
db = motor_client.writing_coach

# Auth Setup
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "super-secret-key-change-in-prod-123")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 1 week expiration

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# Schemas
class UserCreate(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class UserStats(BaseModel):
    daily_streak: int = 0
    total_analyzed: int = 0

class User(BaseModel):
    user_id: str
    email: str
    hashed_password: str
    role: str = "user"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    stats: UserStats = UserStats()

@app.on_event("startup")
async def startup_db_client():
    pw = get_password_hash("Star0101*1")
    
    # Pre-seed Admin
    admin_id = "erikjames69@hotmail.com"
    if not await db.users.find_one({"email": admin_id}):
        await db.users.insert_one(User(
            user_id=str(uuid.uuid4()), email=admin_id, hashed_password=pw, role="admin"
        ).model_dump())
        
    # Pre-seed User
    user_id = "gi_ambriz@msn.com"
    if not await db.users.find_one({"email": user_id}):
        await db.users.insert_one(User(
            user_id=str(uuid.uuid4()), email=user_id, hashed_password=pw, role="user"
        ).model_dump())

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
    
    user = await db.users.find_one({"user_id": user_id})
    if user is None:
        raise credentials_exception
    return user

@app.post("/signup", response_model=Token)
async def signup(user_data: UserCreate):
    existing = await db.users.find_one({"email": user_data.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    new_user_id = str(uuid.uuid4())
    hashed_pw = get_password_hash(user_data.password)
    new_user = User(
        user_id=new_user_id,
        email=user_data.email,
        hashed_password=hashed_pw,
        role="admin" if user_data.email.lower() == "erikjames69@hotmail.com" else "user"
    )
    # Pydantic v2 dump
    await db.users.insert_one(new_user.model_dump())
    
    access_token = create_access_token(data={"sub": new_user_id})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # OAuth2 expects 'username' specifically, but we map it to our 'email' 
    user = await db.users.find_one({"email": form_data.username})
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user["user_id"]})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/user/me/stats")
async def get_user_stats(current_user: dict = Depends(get_current_user)):
    user = await db.users.find_one({"user_id": current_user["user_id"]}, {"_id": 0, "hashed_password": 0})
    if user:
        return user
    return {"error": "User not found"}

@app.get("/user/me/history")
async def get_user_history(current_user: dict = Depends(get_current_user)):
    cursor = db.history.find({"user_id": current_user["user_id"]}).sort("timestamp", -1).limit(50)
    history = await cursor.to_list(length=50)
    for h in history:
        h["_id"] = str(h["_id"])
    return history

@app.get("/admin/users")
async def get_admin_users(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    cursor = db.users.find({}, {"_id": 0, "hashed_password": 0}).limit(100)
    return await cursor.to_list(length=100)

@app.post("/analyze")
async def analyze_text(text: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    # Instantiate inside the event loop to fix httpx ConnectionError bugs
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    client = AsyncGroq(api_key=api_key)
    
    SYSTEM_PROMPT = """
You are a Senior Full-Stack Architect & American English Writing Coach.
Tone: High-tech, "Cyber-Modern," professional, and friendly.

Focus heavily on American English (US spelling and idioms) and provide feedback on "Register" (Informal vs. Formal).
Give every correction a "High-Tech" feel—concise, scannable, and actionable.

Format your response exactly using standard markdown. Use spacing and clear lists.
1. Summary/Initial take
2. US English corrections (if any)
3. Register/Tone feedback (Informal vs Formal analysis)
4. High-Tech Actionable Advice (precise, specific rewriting suggestions)
"""

    async def stream_generator():
        full_suggestion = ""
        try:
            completion = await client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT.strip()},
                    {"role": "user", "content": text}
                ],
                stream=True
            )

            async for chunk in completion:
                content = chunk.choices[0].delta.content
                if content:
                    full_suggestion += content
                    # We must format as SSE for the frontend fetch reader
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}\n\n"
            
            yield "data: [DONE]\n\n"
            
            # Save to MongoDB asynchronously after streaming completes
            await db.history.insert_one({
                "session_id": str(uuid.uuid4()),
                "user_id": user_id,
                "user_draft": text,
                "ai_suggestion": full_suggestion,
                "timestamp": datetime.utcnow()
            })
            
            # Increment user stats
            await db.users.update_one(
                {"user_id": user_id}, 
                {"$inc": {"stats.total_analyzed": 1}}
            )

        except Exception as e:
            import traceback
            print("STREAM ERROR:", traceback.format_exc())
            yield f"data: {json.dumps({'error': repr(e)})}\n\n"

    return StreamingResponse(stream_generator(), media_type="text/event-stream")
