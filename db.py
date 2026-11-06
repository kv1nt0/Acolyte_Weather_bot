import aiosqlite
import os

DB_PATH = os.getenv("DB_PATH", "weather_bot.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_locations (
                user_id INTEGER,
                location_name TEXT,
                lat REAL,
                lon REAL,
                PRIMARY KEY (user_id, location_name)
            )
        """)
        await db.commit()

async def add_location(user_id: int, name: str, lat: float, lon: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_locations (user_id, location_name, lat, lon) VALUES (?, ?, ?, ?)",
            (user_id, name, lat, lon)
        )
        await db.commit()

async def get_user_locations(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT location_name, lat, lon FROM user_locations WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchall()

async def get_last_location(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT location_name, lat, lon FROM user_locations WHERE user_id = ? ORDER BY rowid DESC LIMIT 1", (user_id,)) as cursor:
            return await cursor.fetchone()

async def delete_location(user_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM user_locations WHERE user_id = ? AND location_name = ?",
            (user_id, name)
        )
        await db.commit()
