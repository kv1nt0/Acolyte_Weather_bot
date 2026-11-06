import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# Configuration thresholds
RAIN_THRESHOLD_PC = 70
WIND_THRESHOLD_KMH = 15

class WeatherAlert:
    def __init__(self, alert_type: str, condition: str, level: str = "info"):
        self.alert_type = alert_type  # "rain", "wind", "severe"
        self.condition = condition
        self.level = level  # "info", "warning", "danger"

    def __str__(self) -> str:
        icons = {
            "rain": "🌧",
            "wind": "💨",
            "severe": "⚠️"
        }
        return f"{icons.get(self.alert_type, 'ℹ️')} {self.alert_type.upper()}: {self.condition}"

class AlertChecker:
    """Check weather data against thresholds and generate alerts."""

    @staticmethod
    async def check_rain_probability(hourly_data: Dict[str, Any]) -> List[WeatherAlert]:
        """Check for high rain probability (warning 1 hour before)."""
        alerts = []

        precipitation_probs = hourly_data.get("hourly", {}).get("precipitation_probability", [])
        # We need 24h+ forecast, take first ~25 entries (today + next 24h)
        probs = precipitation_probs[:25]

        # Check if any hour has rain >= threshold
        for i, prob in enumerate(probs):
            if prob >= RAIN_THRESHOLD_PC:
                hours_from_now = i
                alert_time = f"{hours_from_now + 1} час" if hours_from_now == 0 else f"{hours_from_now + 1} часа"
                condition = "Большая вероятность дождя"
                alerts.append(WeatherAlert(
                    alert_type="rain",
                    condition=condition,
                    level="warning" if hours_from_now >= 1 else "info"
                ))
                logger.info(f"Rain alert for user: prob={prob} hours={hours_from_now+1}")

        return alerts

    @staticmethod
    async def check_wind_speed(current_data: Dict[str, Any]) -> List[WeatherAlert]:
        """Check for strong winds/gusts."""
        alerts = []

        wind_speed = current_data.get("wind", 0)
        wind_gusts = current_data.get("wind_gusts", None)

        # Check current wind speed
        if wind_speed >= WIND_THRESHOLD_KMH:
            gusts_str = f" (порывы до {wind_gusts} км/ч)" if wind_gusts else ""
            alerts.append(WeatherAlert(
                alert_type="wind",
                condition=f"Сильный ветер ({wind_speed} км/ч{gusts_str})",
                level="warning"
            ))

        # Check gusts
        if wind_gusts and wind_gusts >= WIND_THRESHOLD_KMH * 1.5:
            alerts.append(WeatherAlert(
                alert_type="wind",
                condition=f"Очень сильные порывы ветра ({wind_gusts} км/ч)",
                level="danger"
            ))

        return alerts

    @staticmethod
    async def check_combined_conditions(current_data: Dict[str, Any], alert_history: List[str] = None) -> List[WeatherAlert]:
        """Check both rain and wind conditions together."""
        alerts = []

        # Check rain
        rain_alerts = await AlertChecker.check_rain_probability(last_hourly_data)
        if rain_alerts:
            alerts.extend(rain_alerts)

        # Check wind
        wind_alerts = await AlertChecker.check_wind_speed(current_data)

        # Severe weather alert
        if rain_alerts and wind_alerts:
            alerts.append(WeatherAlert(
                alert_type="severe",
                condition="Штормовое предупреждение: сильный ветер и вероятность дождя",
                level="danger"
            ))

        return alerts