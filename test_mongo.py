import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def main():
    try:
        client = AsyncIOMotorClient("mongodb://localhost:27017", serverSelectionTimeoutMS=2000)
        await client.server_info()
        print("Mongo connected.")
    except Exception as e:
        print("Mongo error:", repr(e))

asyncio.run(main())
