import asyncio
import uuid
import bcrypt
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
import certifi

MONGO_URI = "mongodb+srv://ErikRobles:Star0101*1@cluster0.pa3mj3x.mongodb.net/writing_coach?retryWrites=true&w=majority&appName=Cluster0"

def get_password_hash(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

async def seed_users():
    client = AsyncIOMotorClient(MONGO_URI, tlsCAFile=certifi.where())
    db = client.writing_coach
    
    users_to_create = [
        {"email": "free@test.com", "tier": "free", "role": "free"},
        {"email": "basic@test.com", "tier": "basic", "role": "basic"},
        {"email": "pro@test.com", "tier": "pro", "role": "pro"},
        {"email": "premium@test.com", "tier": "premium", "role": "premium"},
        {"email": "admin@test.com", "tier": "corporate", "role": "admin"}
    ]
    
    password = "password123"
    hashed_pw = get_password_hash(password)
    
    for u in users_to_create:
        existing = await db.users.find_one({"email": u["email"]})
        if existing:
            print(f"User {u['email']} already exists. Updating role and tier.")
            await db.users.update_one(
                {"email": u["email"]},
                {"$set": {"role": u["role"], "stats.current_tier": u["tier"]}}
            )
        else:
            print(f"Creating user {u['email']}")
            new_user = {
                "user_id": str(uuid.uuid4()),
                "email": u["email"],
                "hashed_password": hashed_pw,
                "role": u["role"],
                "created_at": datetime.utcnow(),
                "stats": {
                    "daily_streak": 0,
                    "total_analyzed": 0,
                    "total_practice_sessions": 0,
                    "average_spelling": 0.0,
                    "average_grammar": 0.0,
                    "average_style": 0.0,
                    "last_active": None,
                    "current_tier": u["tier"],
                    "monthly_tokens_used": 0,
                    "last_token_reset": datetime.utcnow()
                }
            }
            await db.users.insert_one(new_user)
            
    print("Seeding complete.")

if __name__ == "__main__":
    asyncio.run(seed_users())
