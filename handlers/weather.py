from aiogram import Router, types, F
from datetime import datetime, date, timedelta, timezone
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Location, ReplyKeyboardMarkup, KeyboardButton
import asyncio
import logging

from services.weather import OpenMeteoProvider, MetNorwayProvider
from services.alerts import WeatherAlert, AlertChecker
from db import add_location, get_user_locations, init_db, get_last_location, delete_location

logger = logging.getLogger(__name__)
router = Router()
weather_provider = OpenMeteoProvider()
alert_checker = AlertChecker()
RAIN_THRESHOLD_PC = 70

# Store last weather data per user: {user_id: {current: data, hourly: data, daily: data, lat: float, lon: float, location_name: str}}
weather_cache = {}

def weather_code_description(code) -> str:
    """Open-Meteo WMO weather code -> short Russian description with emoji."""
    if code == 0: return "☀️ Ясно"
    if code == 1: return "🌤 Малооблачно"
    if code == 2: return "⛅ Облачно"
    if code == 3: return "☁️ Пасмурно"
    if code in (45, 48): return "🌫 Туман"
    if 51 <= code <= 57: return "🌦 Морось"
    if 61 <= code <= 67: return "🌧 Дождь"
    if 71 <= code <= 77: return "❄️ Снег"
    if 80 <= code <= 82: return "🌧 Ливень"
    if 95 <= code <= 99: return "⛈ Гроза"
    return "🌈 Неизвестно"

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    """Start the bot and show main options."""
    reply_kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📍 Геолокация", request_location=True)],
        [KeyboardButton(text="🌤 Погода"), KeyboardButton(text="🔄 Обновить")],
        [KeyboardButton(text="📍 Мои города")]
    ], resize_keyboard=True)

    await message.answer(
        "🌤 Погодный бот\n\n"
        "Я могу показать:\n"
        "• Текущую погоду с прогнозом\n"
        "• Почасовой и дневной прогноз\n"
        "• Уведомления о сильной погоде\n\n"
        "Отправьте мне геолокацию или название города, "
        "или воспользуйтесь кнопками ниже.",
        reply_markup=reply_kb
    )

@router.message(F.text == "📍 Мои города")
async def handle_my_cities(message: types.Message):
    """Show the list of saved cities (same as /locations)."""
    await _show_locations_list(message, message.from_user.id)

async def fetch_and_show_weather(message: types.Message, lat: float, lon: float, location_name: str, save: bool = False, user_id: int = None):
    """Fetch weather data and show it to the user.

    user_id: who to key the weather cache / save under. Defaults to message.from_user.id.
    Callback handlers must pass query.from_user.id explicitly, because they forward
    query.message — a bot-sent message whose from_user is not the clicking user.
    """
    uid = user_id if user_id is not None else message.from_user.id
    try:
        current_data = await weather_provider.get_current_weather(lat, lon)
        hourly_data = await weather_provider.get_hourly_forecast(lat, lon)
        daily_data = await weather_provider.get_forecast(lat, lon)
        sea_temp = await weather_provider.get_sea_temperature(lat, lon)
        air_quality = await weather_provider.get_air_quality(lat, lon)

        current_data["location_name"] = location_name
        current_data["sea_temp"] = sea_temp
        current_data["air_quality"] = air_quality

        # Cache the data under the real user id so the save button can find it
        weather_cache[uid] = {
            "current": current_data,
            "hourly": hourly_data,
            "daily": daily_data,
            "lat": lat,
            "lon": lon,
            "location_name": location_name
        }

        if save:
            await add_location(uid, location_name, lat, lon)
            await message.answer(f"✅ Город {location_name} сохранен!")

        await show_weather_options(message, current_data, hourly_data, daily_data, location_name)

    except Exception as e:
        logger.error(f"Error fetching weather: {e}")
        await message.answer("Произошла ошибка при получении погоды. Попробуйте позже.")

async def show_weather_options(message: types.Message, current_data: dict, hourly_data: dict, daily_data: dict, location_name: str):
    """Show current weather with navigation buttons."""
    # Format current weather
    wind_gusts = current_data.get("wind_gusts")
    wind_str = f"Ветер: {current_data['wind']} км/ч"
    if wind_gusts:
        wind_str += f" (порывы до {wind_gusts} км/ч)"

    # Get data for "3 hours later" (index 3 assuming hourly data starts now)
    hourly = hourly_data.get("hourly", {})
    temps = hourly.get("temperature_2m", [])
    precip_prob = hourly.get("precipitation_probability", [])
    precip = hourly.get("precipitation", [])

    temp_3h_str = ""
    if len(temps) > 3:
        temp_3h = temps[3]
        temp_3h_str = f" через 3 ч будет {temp_3h}°C"

    precip_3h_str = ""
    if len(precip_prob) > 3 and len(precip) > 3:
        precip_3h_str = f"\n🌧 Дождь: {precip_prob[3]}% | {precip[3]} мм осадков в течении 3 ч"

    sea_temp = current_data.get("sea_temp")
    sea_str = f"\n🌊 Море: {sea_temp}°C" if sea_temp is not None else ""

    # UV index
    uv_index = current_data.get("uv_index")
    uv_str = ""
    if uv_index is not None:
        if uv_index <= 2:
            uv_level = "низкий"
        elif uv_index <= 5:
            uv_level = "умеренный"
        elif uv_index <= 7:
            uv_level = "высокий"
        elif uv_index <= 10:
            uv_level = "очень высокий"
        else:
            uv_level = "экстремальный"
        uv_str = f"\n☀️ УФ: {uv_index} ({uv_level})"

    # Air quality
    air_quality = current_data.get("air_quality")
    aq_str = ""
    if air_quality and air_quality.get("pm2_5") is not None:
        pm2_5 = air_quality["pm2_5"]
        aqi = air_quality.get("european_aqi")
        if pm2_5 <= 12:
            aq_level = "хороший"
        elif pm2_5 <= 35:
            aq_level = "умеренный"
        elif pm2_5 <= 55:
            aq_level = "нездоровый для чувствительных"
        elif pm2_5 <= 150:
            aq_level = "нездоровый"
        else:
            aq_level = "опасный"
        aq_str = f"\n🌫 Воздух: {aq_level} (PM2.5: {pm2_5})"

    response = (
        f"📍 {location_name}  {weather_code_description(current_data.get('condition_code', -1))}\n"
        f"🌡 {current_data['temp']}°C{temp_3h_str}\n"
        f"💧 Влажность: {current_data['humidity']}%{precip_3h_str}\n"
        f"💨 {wind_str}"
        f"{sea_str}"
        f"{uv_str}"
        f"{aq_str}"
    )

    # Add alert information if present
    alerts = current_data.get("alerts", [])
    if alerts:
        response += "\n\n"
        for alert in alerts:
            response += f"⚠️ {alert}\n"

    # Add buttons for different views
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Почасовой прогноз", callback_data="h_0")],
        [InlineKeyboardButton(text="📅 Дневной прогноз", callback_data="d_0")]
    ])

    await message.answer(response, reply_markup=keyboard)

@router.message(F.location)
async def handle_location(message: types.Message):
    """Handle location sharing."""
    lat = message.location.latitude
    lon = message.location.longitude
    await message.answer("🔍 Получаю данные по вашему местоположению...")
    await fetch_and_show_weather(message, lat, lon, "Ваше местоположение")

@router.message(F.text.in_({"🌤 Погода", "🔄 Обновить"}))
async def handle_weather_request(message: types.Message):
    """Handle requests for weather/update using the last saved location."""
    location = await get_last_location(message.from_user.id)
    if not location:
        await message.answer("Сначала отправьте геолокацию или название города")
        return

    # location is (location_name, lat, lon)
    await fetch_and_show_weather(message, location[1], location[2], location[0])

@router.message(Command("locations"))
async def cmd_locations(message: types.Message):
    """Show list of saved locations."""
    await _show_locations_list(message, message.from_user.id)


@router.message(Command("weather"))
async def cmd_weather(message: types.Message):
    """Show weather for the last saved city (like the "🌤 Погода" button)."""
    location = await get_last_location(message.from_user.id)
    if not location:
        await message.answer("Сначала отправьте геолокацию или название города")
        return

    # location is (location_name, lat, lon)
    await fetch_and_show_weather(message, location[1], location[2], location[0])


# Store geo candidates per user until the user picks one: {user_id: [geo_result, ...]}
geo_candidates = {}


def geo_sort_results(results: list, query: str) -> list:
    """Priority: exact name matches first, then API relevance order (population-based)."""
    q = query.strip().lower()
    exact = [r for r in results if str(r.get("name", "")).lower() == q]
    rest = [r for r in results if str(r.get("name", "")).lower() != q]
    return exact + rest


def geo_label(r: dict) -> str:
    """'Poti, Georgia' style label from a geocoding result."""
    name = r.get("name", "")
    country = r.get("country") or ""
    return f"{name}, {country}" if country else name


@router.message(F.text)
async def handle_text(message: types.Message):
    """Handle text input (city name) via geocoding."""
    city_name = message.text.strip()
    if not city_name:
        return

    await message.answer(f"🔍 Ищу город: {city_name}...")
    results = await weather_provider.geocode(city_name)
    if not results:
        await message.answer("❌ Город не найден. Попробуйте ввести название иначе.")
        return

    # One match -> show weather right away
    if len(results) == 1:
        r = results[0]
        await fetch_and_show_weather(message, r["lat"], r["lon"], geo_label(r))
        return

    # Multiple matches -> let the user choose
    results = geo_sort_results(results, city_name)
    geo_candidates[message.from_user.id] = results
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=geo_label(r), callback_data=f"geo:{i}")]
        for i, r in enumerate(results[:5])
    ])
    await message.answer(
        f"🤔 Нашел несколько мест «{city_name}»:\nВыберите нужный город:",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("geo:"))
async def handle_geo_select(query: types.CallbackQuery):
    """Show weather for the city chosen from the geo candidates list."""
    index = int(query.data.split(":", 1)[1])
    results = geo_candidates.get(query.from_user.id, [])
    if index >= len(results):
        await query.answer("Данные устарели. Введите город заново.", show_alert=True)
        return

    r = results[index]
    del geo_candidates[query.from_user.id]
    await query.answer()
    await fetch_and_show_weather(query.message, r["lat"], r["lon"], geo_label(r), save=True, user_id=query.from_user.id)


@router.callback_query(F.data == "saved_locations")
async def handle_saved_locations(query: types.CallbackQuery):
    """Show list of saved locations via inline button."""
    await _show_locations_list(query.message, query.from_user.id)
    await query.answer()


async def _show_locations_list(message: types.Message, user_id: int):
    """Render the list of saved locations with inline buttons."""
    locations = await get_user_locations(user_id)

    if not locations:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗺 Сохранить город", callback_data="save_location")]
        ])
        await message.answer(
            "📭 У вас пока нет сохранённых городов.\n"
            "Отправьте геолокацию или название города, чтобы добавить его.",
            reply_markup=keyboard
        )
        return

    keyboard_rows = []
    for name, lat, lon in locations:
        keyboard_rows.append([
            InlineKeyboardButton(text=f"📍 {name}", callback_data=f"loc_sel:{name}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"loc_del:{name}")
        ])
    keyboard_rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    await message.answer(
        "📋 Ваши сохранённые города:\n"
        "Нажмите на город, чтобы увидеть погоду.",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("loc_sel:"))
async def handle_location_select(query: types.CallbackQuery):
    """Show weather for the selected saved city."""
    name = query.data.split(":", 1)[1]
    locations = await get_user_locations(query.from_user.id)
    found = None
    for loc_name, lat, lon in locations:
        if loc_name == name:
            found = (lat, lon, loc_name)
            break

    if not found:
        await query.answer("Город не найден в списке.", show_alert=True)
        return

    lat, lon, loc_name = found
    await query.answer(f"Загружаю погоду для {loc_name}...")
    await fetch_and_show_weather(query.message, lat, lon, loc_name, save=True, user_id=query.from_user.id)


@router.callback_query(F.data.startswith("loc_del:"))
async def handle_location_delete(query: types.CallbackQuery):
    """Delete a saved city."""
    name = query.data.split(":", 1)[1]
    await delete_location(query.from_user.id, name)
    await query.answer(f"✅ {name} удалён из списка.")
    # Refresh the list
    await _show_locations_list(query.message, query.from_user.id)


@router.callback_query(F.data == "back_to_main")
async def handle_back_to_main(query: types.CallbackQuery):
    """Return to main menu."""
    await cmd_start(query.message)
    await query.answer()

@router.callback_query(F.data == "save_location")
async def handle_save_location(query: types.CallbackQuery):
    """Save the current location in cache to the database."""
    user_id = query.from_user.id
    weather_data = weather_cache.get(user_id)

    if not weather_data or "lat" not in weather_data or "lon" not in weather_data:
        await query.answer("Нет данных для сохранения. Сначала найдите город.", show_alert=True)
        return

    lat = weather_data["lat"]
    lon = weather_data["lon"]
    name = weather_data["location_name"]

    await add_location(user_id, name, lat, lon)
    await query.answer(f"✅ {name} сохранен!", show_alert=False)

@router.callback_query(F.data.startswith(("h_", "d_")))
async def handle_weather_view(query: types.CallbackQuery):
    """Handle inline button callbacks for weather views."""
    user_id = query.from_user.id
    data = query.data
    view_type = data.split("_")[0]  # h_ for hourly, d_ for daily

    weather_data = weather_cache.get(user_id)

    if not weather_data:
        await query.message.answer("Нет сохраненных данных. Попробуйте сначала отправить город.")
        await query.answer()
        return

    if view_type == "h":
        # Hourly forecast: next 24 hours starting from current hour
        hourly_outer = weather_data.get("hourly", {})
        hourly = hourly_outer.get("hourly", hourly_outer) or {}
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        precip = hourly.get("precipitation", [])
        precip_prob = hourly.get("precipitation_probability", [])
        cloud_cover = hourly.get("cloud_cover", [])
        wind_speed = hourly.get("wind_speed_10m", [])
        wind_gusts = hourly.get("wind_gusts_10m", [])

        if not times:
            await query.message.answer("Данные почасового прогноза отсутствуют.")
            await query.answer()
            return

        # Находим индекс текущего часа
        utc_offset = int(hourly_outer.get("utc_offset_seconds", 0) or 0)
        # Текущее время с учетом смещения
        now_local = datetime.now(timezone.utc) + timedelta(seconds=utc_offset)
        # Строка формата Open-Meteo
        target_hour_str = now_local.strftime("%Y-%m-%dT%H:00")

        start_index = 0
        for i, t in enumerate(times):
            if t >= target_hour_str:
                start_index = i
                break

        loc = weather_data['current'].get('location_name', 'Город')
        response = f"📈 Почасовой прогноз для {loc} (на 6 часов):\n\n"

        def get_cloud_icon(c):
            if c <= 20: return "☀️"
            if c <= 50: return "🌤"
            if c <= 80: return "⛅"
            return "☁️"

        for i in range(start_index, min(start_index + 6, len(times))):
            hour = times[i][11:16]
            temp = temps[i] if i < len(temps) else "-"
            cloud = cloud_cover[i] if i < len(cloud_cover) else 0
            prob = precip_prob[i] if i < len(precip_prob) else 0
            mm = precip[i] if i < len(precip) else 0
            w_speed = wind_speed[i] if i < len(wind_speed) else 0

            icon = get_cloud_icon(cloud)

            # Формат: час | темп | облака | ветер | осадки
            line = f"{hour} | 🌡 {temp}°C | {icon} {cloud}% | 💨 {w_speed} км/ч"
            if prob > 0:
                line += f" | 🌧 {prob}%"
            if mm > 0:
                line += f" | 💧 {mm} мм"
            response += line + "\n\n"
        response = response.rstrip("\n")
        await query.message.answer(response)

    elif view_type == "d":
        # Daily forecast: 3 days
        daily_outer = weather_data.get("daily", {})
        daily = daily_outer.get("daily", daily_outer) or {}
        times = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        precip_prob = daily.get("precipitation_probability_max", [])
        weather_codes = daily.get("weather_code", [])
        precip_sum = daily.get("precipitation_sum", [0] * len(times))

        loc = weather_data['current'].get('location_name', 'Город')
        day_labels = ["Сегодня", "Завтра", "Послезавтра"]
        response = f"📅 Дневной прогноз для {loc} (3 дня):\n\n"
        for i in range(min(3, len(times))):
            label = day_labels[i] if i < len(day_labels) else times[i]
            code = weather_codes[i] if i < len(weather_codes) else -1
            desc = weather_code_description(code)
            prob = precip_prob[i] if i < len(precip_prob) else 0
            sum_mm = precip_sum[i] if i < len(precip_sum) else 0

            rain_note = f"вероятность дождя {prob}% | {sum_mm} мм за сутки" if prob > 0 else "без осадков"
            response += f"{label}\n{desc}\n{min_temps[i]}°C … {max_temps[i]}°C\n{rain_note}\n\n"
        response = response.rstrip()
        await query.message.answer(response)

    await query.answer()
