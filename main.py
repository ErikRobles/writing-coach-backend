from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from groq import AsyncGroq
import bcrypt
import jwt
from datetime import datetime, timedelta, time
import os
import json
import uuid
import asyncio
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import aiosmtplib
from email.message import EmailMessage

load_dotenv()

from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from typing import Optional, List, Dict

app = FastAPI()

# SMTP Configuration
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB Setup
MONGODB_URL = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/writing_coach")
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

class ScoreBreakdown(BaseModel):
    spelling: int = 0
    grammar: int = 0
    style: int = 0  # 0-100 score
    detected_style: str = "informal" # formal, semi-formal, informal

class UserStats(BaseModel):
    daily_streak: int = 0
    total_analyzed: int = 0
    total_practice_sessions: int = 0
    average_spelling: float = 0.0
    average_grammar: float = 0.0
    average_style: float = 0.0
    last_active: Optional[datetime] = None

class User(BaseModel):
    user_id: str
    email: str
    hashed_password: str
    role: str = "user"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    stats: UserStats = UserStats()

class PracticeSession(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    text: str
    scores: ScoreBreakdown
    feedback: str
    common_mistakes: List[str] = []
    tips: List[str] = []
    timestamp: datetime = Field(default_factory=datetime.utcnow)

import ssl
import certifi

# Email and Scheduler Logic
async def send_report_to_user(user_id: str, email: str, sessions: List[dict]):
    # Aggregate stats for the report
    total_spelling = sum(s["scores"]["spelling"] for s in sessions)
    total_grammar = sum(s["scores"]["grammar"] for s in sessions)
    count = len(sessions)
    
    avg_spelling = total_spelling / count
    avg_grammar = total_grammar / count
    
    # Prepare data for the chart
    # Get last 7 sessions for the chart labels and data
    chart_sessions = sessions[-7:]
    labels = [s.get("timestamp", datetime.utcnow()).strftime("%m/%d") for s in chart_sessions]
    spelling_data = [s["scores"]["spelling"] for s in chart_sessions]
    grammar_data = [s["scores"]["grammar"] for s in chart_sessions]
    
    # Generate QuickChart URL (Styled to match the app's dark/neon theme)
    chart_config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {
                    "label": "Spelling",
                    "data": spelling_data,
                    "borderColor": "#00F5FF",
                    "backgroundColor": "rgba(0, 245, 255, 0.1)",
                    "fill": True,
                    "borderWidth": 3
                },
                {
                    "label": "Grammar",
                    "data": grammar_data,
                    "borderColor": "#A9FFDF",
                    "backgroundColor": "rgba(169, 255, 223, 0.1)",
                    "fill": True,
                    "borderWidth": 3
                }
            ]
        },
        "options": {
            "title": {"display": True, "text": "Your Writing Progression", "fontColor": "#ffffff"},
            "legend": {"labels": {"fontColor": "#adaaad"}},
            "scales": {
                "yAxes": [{"ticks": {"min": 0, "max": 100, "fontColor": "#adaaad"}, "gridLines": {"color": "#2D2D2D"}}],
                "xAxes": [{"ticks": {"fontColor": "#adaaad"}, "gridLines": {"display": False}}]
            }
        }
    }
    
    import urllib.parse
    encoded_config = urllib.parse.quote(json.dumps(chart_config))
    chart_url = f"https://quickchart.io/chart?c={encoded_config}&bg=0e0e10&w=600&h=300"
    
    all_mistakes = []
    for s in sessions:
        all_mistakes.extend(s.get("common_mistakes", []))
    
    # Simple frequency count for mistakes
    mistake_freq = {}
    for m in all_mistakes:
        mistake_freq[m] = mistake_freq.get(m, 0) + 1
    sorted_mistakes = sorted(mistake_freq.items(), key=lambda x: x[1], reverse=True)[:3]
    
    # Generate tips for the top mistakes (simplistic for now)
    report_content = f"""
    <div style="background-color: #0e0e10; color: #fffbfe; font-family: 'Inter', sans-serif; padding: 40px; border-radius: 20px;">
        <h1 style="color: #a9ffdf; font-family: 'Space Grotesk', sans-serif; border-bottom: 1px solid #48474a; padding-bottom: 20px;">Your Writing Progress Report</h1>
        <p style="font-size: 16px; color: #adaaad;">Hello! Here's your performance snapshot:</p>
        
        <div style="background: rgba(169, 255, 223, 0.05); padding: 20px; border-radius: 12px; margin-bottom: 30px; border: 1px solid rgba(169, 255, 223, 0.1);">
            <ul style="list-style: none; padding: 0; margin: 0;">
                <li style="margin-bottom: 10px;"><strong>Practice Sessions:</strong> {count}</li>
                <li style="margin-bottom: 10px;"><strong>Avg. Spelling Score:</strong> <span style="color: #00F5FF;">{avg_spelling:.1f}%</span></li>
                <li><strong>Avg. Grammar Score:</strong> <span style="color: #A9FFDF;">{avg_grammar:.1f}%</span></li>
            </ul>
        </div>
        
        <h3 style="color: #ac89ff; font-family: 'Space Grotesk', sans-serif;">Linear Improvement Progression:</h3>
        <div style="margin: 20px 0; border-radius: 12px; overflow: hidden; border: 1px solid #48474a;">
            <img src="{chart_url}" alt="Writing Progress Chart" style="width: 100%; max-width: 600px; display: block;" />
        </div>
        
        <h3 style="color: #ff51fa; font-family: 'Space Grotesk', sans-serif; margin-top: 40px;">Common Mistakes to Watch:</h3>
        <ul style="color: #adaaad; font-size: 15px;">
            {''.join([f"<li style='margin-bottom: 8px;'><strong style='color: #fff;'>{m[0]}</strong> (occurred {m[1]} times)</li>" for m in sorted_mistakes])}
        </ul>
        
        <p style="margin-top: 40px; font-size: 14px; font-style: italic; color: #48474a;">Keep practicing to reach your goals! - Your AI Writing Coach</p>
    </div>
    """
    
    if SMTP_USER and SMTP_PASS:
        message = EmailMessage()
        message["From"] = SMTP_FROM
        message["To"] = email
        message["Subject"] = "Your Writing Coach Progress Report"
        message.set_content(report_content, subtype="html")
        
        # Create a secure SSL context using certifi
        context = ssl.create_default_context(cafile=certifi.where())
        
        await aiosmtplib.send(
            message,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER,
            password=SMTP_PASS,
            start_tls=True if SMTP_PORT == 587 else False,
            tls_context=context
        )
        return True
    return False

async def send_weekly_report():
    print(f"[{datetime.now()}] Starting weekly report generation...")
    cursor = db.users.find({})
    async for user in cursor:
        user_id = user["user_id"]
        email = user["email"]
        
        # Get sessions from the last 7 days
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        cursor_sessions = db.practice_history.find({
            "user_id": user_id,
            "timestamp": {"$gte": seven_days_ago}
        }).sort("timestamp", 1)
        
        sessions = await cursor_sessions.to_list(length=100)
        if not sessions:
            continue
            
        try:
            await send_report_to_user(user_id, email, sessions)
            print(f"Sent weekly report to {email}")
        except Exception as e:
            print(f"Failed to send weekly email to {email}: {e}")

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

@app.post("/user/me/test-email")
async def trigger_test_email(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    email = current_user["email"]
    
    # Get any sessions (ignoring the 7-day limit for the test)
    cursor_sessions = db.practice_history.find({
        "user_id": user_id
    }).sort("timestamp", 1).limit(10)
    
    sessions = await cursor_sessions.to_list(length=10)
    
    if not sessions:
        # Create a dummy session for testing if none exist
        sessions = [{
            "scores": {"spelling": 85, "grammar": 90, "style": 80},
            "common_mistakes": ["Sample mistake"],
            "timestamp": datetime.utcnow()
        }]
        
    try:
        success = await send_report_to_user(user_id, email, sessions)
        if success:
            return {"message": f"Test email sent successfully to {email}"}
        else:
            return {"error": "SMTP credentials not configured"}
    except Exception as e:
        import traceback
        print("EMAIL TEST ERROR:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

scheduler = AsyncIOScheduler()
scheduler.add_job(send_weekly_report, CronTrigger(day_of_week='mon', hour=9, minute=0))

@app.on_event("startup")
async def startup_db_client():
    scheduler.start()
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

@app.post("/practice")
async def practice_session(text: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    client = AsyncGroq(api_key=api_key)
    
    PRACTICE_PROMPT = """
You are a Writing Coach. Analyze the user's input for Spelling, Grammar, and Style (Formal, Semi-Formal, Informal).
You MUST respond with a JSON object ONLY. Do not include any other text.

The JSON should have this structure:
{
  "scores": {
    "spelling": <int 0-100>,
    "grammar": <int 0-100>,
    "style": <int 0-100>,
    "detected_style": "<formal|semi-formal|informal>"
  },
  "feedback": "<detailed feedback string>",
  "common_mistakes": ["<mistake 1>", "<mistake 2>"],
  "tips": ["<tip 1>", "<tip 2>"]
}
"""

    try:
        completion = await client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": PRACTICE_PROMPT.strip()},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"}
        )

        result_json = json.loads(completion.choices[0].message.content)
        
        # Save practice session
        session = PracticeSession(
            user_id=user_id,
            text=text,
            scores=ScoreBreakdown(**result_json["scores"]),
            feedback=result_json["feedback"],
            common_mistakes=result_json.get("common_mistakes", []),
            tips=result_json.get("tips", [])
        )
        
        await db.practice_history.insert_one(session.model_dump())
        
        # Update user stats
        await db.users.update_one(
            {"user_id": user_id},
            {
                "$inc": {
                    "stats.total_practice_sessions": 1,
                    "stats.total_analyzed": 1
                },
                "$set": {
                    "stats.last_active": datetime.utcnow()
                }
            }
        )
        
        return result_json

    except Exception as e:
        import traceback
        print("PRACTICE ERROR:", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/user/me/practice-history")
async def get_practice_history(current_user: dict = Depends(get_current_user)):
    cursor = db.practice_history.find({"user_id": current_user["user_id"]}).sort("timestamp", 1).limit(100)
    history = await cursor.to_list(length=100)
    for h in history:
        h["_id"] = str(h["_id"])
    return history

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
                model="llama-3.3-70b-versatile",
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
