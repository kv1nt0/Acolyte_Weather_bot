import aiohttp
import os
import re
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Cyrillic (Russian) -> Latin transliteration for language-independent city name matching
_CYR_TO_LAT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya"
})

def has_cyrillic(name: str) -> bool:
    return any("Ѐ" <= ch <= "ӿ" for ch in name)

def normalize_city_name(name: str) -> str:
    """Language- and case-insensitive key for comparing city names.

    "Батуми", "батуми" and "Batumi" all normalize to "batumi".
    """
    n = name.lower().strip().translate(_CYR_TO_LAT) if has_cyrillic(name) else name.lower().strip()
    return re.sub(r"\s+", " ", n)


# Assuming USER_AGENT is set in environment for MET Norway
USER_AGENT = os.getenv("USER_AGENT", "WeatherBot/1.0 (https://t.me/your_bot_username)")

def name_search_variants(name: str) -> List[str]:
    """Order of geocode attempts: original, title case, lower, Latin transliteration.

    Deduped, order preserved. "батуми" -> ["батуми", "Батуми", "batumi", "Batumi"].
    """
    variants = [name, name.title(), name.lower()]
    if has_cyrillic(name):
        lat = name.strip().lower().translate(_CYR_TO_LAT)
        variants.extend([lat, lat.title()])
    seen, unique = set(), []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            unique.append(v)
    return unique

class WeatherProvider(ABC):
    @abstractmethod
    async def get_current_weather(self, lat: float, lon: float) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_forecast(self, lat: float, lon: float, days: int = 3) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_hourly_forecast(self, lat: float, lon: float) -> Dict[str, Any]:
        pass

class OpenMeteoProvider(WeatherProvider):
    BASE_URL = "https://api.open-meteo.com/v1/forecast"
    MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
    AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
    GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
    AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

    async def geocode(self, name: str) -> List[Dict[str, Any]]:
        """Find locations by name using Open-Meteo Geocoding API."""
        candidates = name_search_variants(name)
        logger.info(f"Geocoding search candidates: {candidates}")
        results = []
        for candidate in candidates:
            found = await self._geocode_once(candidate)
            if found:
                # Add all found items to results
                for item in found:
                    if item not in results:
                        results.append(item)
            # Limit results
            if len(results) >= 5:
                break
        return results

    async def _geocode_once(self, name: str) -> List[Dict[str, Any]]:
        params = {"name": name, "count": 5}
        logger.info(f"Geocoding request: {self.GEOCODING_URL} params={params}")
        async with aiohttp.ClientSession() as session:
            async with session.get(self.GEOCODING_URL, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"Geocoding response: {data}")
                    items = []
                    if data.get("results"):
                        for result in data["results"]:
                            items.append({
                                "name": result.get("name"),
                                "lat": result.get("latitude"),
                                "lon": result.get("longitude"),
                                "admin1": result.get("admin1"),
                                "country": result.get("country")
                            })
                        return items
                else:
                    logger.warning(f"Geocoding failed with status {response.status}")
                return []

    async def get_current_weather(self, lat: float, lon: float) -> Dict[str, Any]:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,wind_gusts_10m,weather_code,uv_index"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(self.BASE_URL, params=params) as response:
                data = await response.json()
                current = data["current"]
                return {
                    "source": "Open-Meteo",
                    "temp": current["temperature_2m"],
                    "feels_like": current["apparent_temperature"],
                    "humidity": current["relative_humidity_2m"],
                    "wind": current["wind_speed_10m"],
                    "wind_gusts": current["wind_gusts_10m"],
                    "condition_code": current["weather_code"],
                    "uv_index": current.get("uv_index"),
                    "location_name": f"{lat}, {lon}" # Default fallback
                }

    async def get_sea_temperature(self, lat: float, lon: float) -> Optional[float]:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "sea_surface_temperature"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.MARINE_URL, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("current", {}).get("sea_surface_temperature")
        except Exception as e:
            logger.warning(f"Sea temperature request failed: {e}")
        return None

    async def get_air_quality(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        """Fetch air quality data (PM2.5 and European AQI) from Open-Meteo Air Quality API."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "pm2_5,european_aqi",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.AIR_QUALITY_URL, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        hourly = data.get("hourly", {})
                        pm2_5_values = hourly.get("pm2_5", [])
                        aqi_values = hourly.get("european_aqi", [])
                        if pm2_5_values and pm2_5_values[0] is not None:
                            return {
                                "pm2_5": pm2_5_values[0],
                                "european_aqi": aqi_values[0] if aqi_values and aqi_values[0] is not None else None,
                            }
        except Exception as e:
            logger.warning(f"Air quality request failed: {e}")
        return None

    async def get_forecast(self, lat: float, lon: float, days: int = 3) -> Dict[str, Any]:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,precipitation_sum",
            "forecast_days": days
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(self.BASE_URL, params=params) as response:
                return await response.json()

    async def get_hourly_forecast(self, lat: float, lon: float) -> Dict[str, Any]:
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,cloud_cover,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m",
            "timezone": "auto"
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(self.BASE_URL, params=params) as response:
                return await response.json()

class MetNorwayProvider(WeatherProvider):
    BASE_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

    async def _get_headers(self) -> Dict[str, str]:
        return {"User-Agent": USER_AGENT}

    async def get_current_weather(self, lat: float, lon: float) -> Dict[str, Any]:
        params = {"lat": lat, "lon": lon}
        async with aiohttp.ClientSession(headers=await self._get_headers()) as session:
            async with session.get(self.BASE_URL, params=params) as response:
                data = await response.json()
                # MET Norway structure is more complex; extracting relevant fields
                timeseries = data["properties"]["timeseries"][0]["data"]
                instant = timeseries["instant"]["details"]
                return {
                    "source": "MET Norway",
                    "temp": instant["air_temperature"],
                    "feels_like": None,  # Not directly in 'instant'
                    "humidity": instant["relative_humidity"],
                    "wind": instant["wind_speed"],
                    "wind_gusts": instant.get("wind_speed_of_gust"),
                    "condition_code": timeseries["next_1_hours"]["summary"]["symbol_code"],
                    "location_name": f"{lat}, {lon}" # Default fallback
                }

    async def get_forecast(self, lat: float, lon: float, days: int = 3) -> Dict[str, Any]:
        # MET Norway API structure implies we get it all in one,
        # daily aggregation needs logic
        params = {"lat": lat, "lon": lon}
        async with aiohttp.ClientSession(headers=await self._get_headers()) as session:
            async with session.get(self.BASE_URL, params=params) as response:
                return await response.json()

    async def get_hourly_forecast(self, lat: float, lon: float) -> Dict[str, Any]:
        params = {"lat": lat, "lon": lon}
        async with aiohttp.ClientSession(headers=await self._get_headers()) as session:
            async with session.get(self.BASE_URL, params=params) as response:
                return await response.json()
