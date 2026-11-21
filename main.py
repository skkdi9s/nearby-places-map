import os
import sys
import time

import folium
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------- Налаштування ----------
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",  # запасне дзеркало
]
REQUEST_TIMEOUT = 30       # секунд на один запит
OVERPASS_RETRIES = 2       # повторів на кожне дзеркало
MAX_RADIUS = 5000          # метрів

AMENITIES = {
    "1": ("Кав'ярні", "cafe"),
    "2": ("Аптеки", "pharmacy"),
    "3": ("Ресторани", "restaurant"),
}

USER_AGENT = os.getenv("Nominatim_User_Agent")
CARTO_KEY = os.getenv("Carto_Api")


# ---------- Мережа ----------
def geocode(address):
    """Повертає (lat, lon, display_name) або None."""
    params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(NOMINATIM_URL, params=params,
                            headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"Помилка запиту до Nominatim: {e}")
        return None
    except ValueError:
        print("Nominatim повернув некоректну відповідь.")
        return None

    if not data:
        return None
    item = data[0]
    return float(item["lat"]), float(item["lon"]), item["display_name"]


def build_query(lat, lon, radius, amenity):
    return f"""
[out:json][timeout:25];
nwr(around:{radius},{lat},{lon})["amenity"="{amenity}"];
out center tags;
"""


def fetch_overpass(query):
    """Пробує дзеркала по черзі з повторами. Повертає список elements або None."""
    headers = {"User-Agent": USER_AGENT}
    for url in OVERPASS_URLS:
        for attempt in range(1, OVERPASS_RETRIES + 1):
            try:
                resp = requests.post(url, data={"data": query},
                                     headers=headers, timeout=REQUEST_TIMEOUT)
            except requests.Timeout:
                print(f"Таймаут ({url}), спроба {attempt}")
            except requests.RequestException as e:
                print(f"Помилка з'єднання ({url}): {e}")
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json().get("elements", [])
                    except ValueError:
                        print("Некоректний JSON від Overpass.")
                elif resp.status_code in (429, 504):
                    print(f"Сервер зайнятий ({resp.status_code}), спроба {attempt}")
                else:
                    print(f"Помилка Overpass {resp.status_code}: {resp.text[:200]}")
                    break  
            time.sleep(2 * attempt)  
    return None


# ---------- Обробка даних ----------
def parse_elements(elements):
    """Перетворює відповідь Overpass на список {name, lat, lon}."""
    places = []
    for el in elements:
        name = el.get("tags", {}).get("name", "Без назви")
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        places.append({"name": name, "lat": lat, "lon": lon})
    return places


def find_places(lat, lon, radius, amenity):
    elements = fetch_overpass(build_query(lat, lon, radius, amenity))
    if elements is None:
        return None
    return parse_elements(elements)


# ---------- Ввід ----------
def ask_address():
    while True:
        address = input("Введіть адресу (наприклад: Львів, Кучера 22): ").strip()
        if not address:
            print("Адреса не може бути порожньою.")
            continue
        result = geocode(address)
        if result:
            return result
        print("Не вдалося знайти координати, уточніть місто або вулицю.")


def ask_radius():
    while True:
        raw = input(f"Введіть радіус у метрах (1–{MAX_RADIUS}): ").strip()
        try:
            radius = int(raw)
        except ValueError:
            print("Введіть ціле число.")
            continue
        if 1 <= radius <= MAX_RADIUS:
            return radius
        print(f"Радіус має бути від 1 до {MAX_RADIUS}.")


def ask_amenity():
    while True:
        print("\nОберіть тип закладу:")
        for key, (label, _) in AMENITIES.items():
            print(f"{key} - {label}")
        choice = input("Оберіть число від 1 до 3: ").strip()
        if choice in AMENITIES:
            return AMENITIES[choice]
        print("Оберіть інший варіант!")


# ---------- Карта ----------
def build_map(lat, lon, radius, places, filename="map.html"):
    attr = ('&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
            '&copy; <a href="https://carto.com/attributions">CARTO</a>')
    if CARTO_KEY:
        tiles = ("https://basemaps.cartocdn.com/rastertiles/dark_all/"
                 f"{{z}}/{{x}}/{{y}}.png?key={CARTO_KEY}")
    else:
        print("Carto_Api не знайдено в .env, використовую OpenStreetMap.")
        tiles, attr = "OpenStreetMap", None

    m = folium.Map(location=[lat, lon], zoom_start=16, tiles=tiles, attr=attr)

    folium.Marker([lat, lon], popup="Ваша точка пошуку",
                  icon=folium.Icon(color="red", icon="home")).add_to(m)
    folium.Circle([lat, lon], radius=radius, color="#3186cc",
                  fill=True, fill_opacity=0.15).add_to(m)

    for p in places:
        folium.Marker([p["lat"], p["lon"]], popup=p["name"], tooltip=p["name"],
                      icon=folium.Icon(color="green", icon="info-sign")).add_to(m)

    m.save(filename)
    return filename


# ---------- Точка входу ----------
def main():
    if not USER_AGENT:
        sys.exit("Додайте Nominatim_User_Agent у файл .env")

    lat, lon, display_name = ask_address()
    print(f"Знайдено точку: {display_name}")
    print(f"Координати: {lat}, {lon}\n")

    radius = ask_radius()
    label, amenity = ask_amenity()

    places = find_places(lat, lon, radius, amenity)
    if places is None:
        sys.exit("Не вдалося отримати дані від Overpass. Спробуйте пізніше.")

    print(f"\n{label}: знайдено {len(places)} у радіусі {radius} м")
    for p in places:
        print(f"- {p['name']} ({p['lat']}, {p['lon']})")

    filename = build_map(lat, lon, radius, places)
    print(f"\nКарту збережено у {filename}")


if __name__ == "__main__":
    main()