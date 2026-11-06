import logging
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, time
from typing import List, Optional
import signal

from services.weather import OpenMeteoProvider
from services.alerts import AlertChecker
from db import add_location, get_user_locations

logger = logging.getLogger(__name__)

class WeatherScheduler:
    """Scheduler for daily notifications and rain alerts."""

    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler()
        self.provider = OpenMeteoProvider()
        self.alert_checker = AlertChecker()

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def shutdown(self, signum, frame):
        """Handle shutdown gracefully."""
        logger.info("Shutdown requested, stopping scheduler...")
        self.scheduler.shutdown()
        raise KeyboardInterrupt()

    async def check_rain_alerts_for_location(self, user_id: int, location_name: str, lat: float, lon: float):
        """Check rain probability for a location during scheduled task."""
        try:
            # Get hourly forecast
            hourly_data = await self.provider.get_hourly_forecast(lat, lon)

            # Check thresholds
            rain_alerts = await self.alert_checker.check_rain_probability(hourly_data)

            if rain_alerts:
                alert_text = f"🌧 Уведомление о погоде ({location_name}): " + " ".join([str(a) for a in rain_alerts])
                await self.bot.send_message(user_id, alert_text)

        except Exception as e:
            logger.error(f"Error checking rain alerts for {location_name}: {e}")

    async def send_daily_weather_notification(self, user_id: int, location_name: str, lat: float, lon: float):
        """Send daily weather notification at set time."""
        try:
            # Get current weather
            current_data = await self.provider.get_current_weather(lat, lon)

            # Get hourly data for trend
            hourly_data = await self.provider.get_hourly_forecast(lat, lon)

            # Generate forecast string
            trend_text = (
                f"📍 {location_name}\n"
                f"🌡 {current_data['temp']}°C  |  💧 {current_data['humidity']}%  |  💨 {current_data['wind']} км/ч "
                f"{'(порывы ' + str(current_data.get('wind_gusts', current_data['wind'])) + ')' if current_data.get('wind_gusts') else ''}\n"
                f"☀️ {current_data.get('condition_code', '—')}"
            )

            # Add hourly trend if available
            temps = hourly_data.get("hourly", {}).get("temperature_2m", [])[:24]
            if temps:
                trend_text += "\n📈 Сегодня: " + " / ".join([f"{t}°C для Hour {i}" for i, t in enumerate(temps[:12])])

            await self.bot.send_message(user_id, trend_text)

        except Exception as e:
            logger.error(f"Error sending daily notification for {location_name}: {e}")

    async def schedule_location(self, user_id: int, location_name: str, lat: float, lon: float):
        """Schedule rain check for specific location."""
        # Schedule to run every hour
        self.scheduler.add_job(
            self.check_rain_alerts_for_location,
            'cron',
            hour='*',
            args=[user_id, location_name, lat, lon],
            id=f"rain_check_{user_id}_{location_name}",
            replacing=True
        )
        logger.info(f"Scheduled rain check for {user_id} - {location_name}")

    async def initialize_locations(self, user_id: int):
        """Initialize scheduled tasks for all saved locations."""
        locations = await get_user_locations(user_id)

        for location in locations:
            location_name, lat, lon = location
            await self.schedule_location(user_id, location_name, lat, lon)

    async def run(self):
        """Start the scheduler."""
        # Schedule daily notifications at 7:00 AM
        self.scheduler.add_job(
            self.daily_notification_task,
            'cron',
            hour=7,
            minute=0,
            id="daily_notification",
            replace_existing=True
        )

        # Enable logging
        self.scheduler.configure(
            timezone='Europe/Moscow',  # Adjust for your timezone
            job_defaults={'coalesce': True, 'max_instances': 1}
        )

        # Start scheduler
        self.scheduler.start()
        logger.info("Scheduler started")

    async def daily_notification_task(self):
        """Run daily updates for all users with saved locations."""
        # This would need to be called for each user
        # In a real implementation, you'd have a database of users who opted in
        logger.info("Running daily notification task (implement user-based dispatching)")
        # TODO: Iterate over all opted-in users and call send_daily_weather_notification

    async def start(self):
        """Main entry point to start scheduler."""
        await self.run()
        try:
            while True:
                await asyncio.sleep(60)  # Keep running
        except KeyboardInterrupt:
            logger.info("Scheduler stopped")