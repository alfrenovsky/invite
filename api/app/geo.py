"""
geo.py - IP Geolocation resolver for silent location detection.
Resolves public client IP addresses to City / Region / Country without user interaction or browser popups.
Includes in-memory TTL caching and private network detection.
"""

import ipaddress
import json
import logging
import time
import urllib.request
from threading import Lock

logger = logging.getLogger("geo")

_CACHE = {}
_CACHE_LOCK = Lock()
_CACHE_TTL = 86400  # 24 hours


def get_client_ip(headers: dict, remote_addr: str = "") -> str:
    """
    Extracts the most reliable public client IP from request headers or remote_addr.
    Inspects X-Forwarded-For, X-Real-IP, and falls back to remote_addr.
    """
    # 1. X-Forwarded-For may contain multiple IPs: "client, proxy1, proxy2"
    xff = headers.get("X-Forwarded-For", "")
    if xff:
        for part in xff.split(","):
            ip_candidate = part.strip()
            if is_public_ip(ip_candidate):
                return ip_candidate

    # 2. X-Real-IP
    x_real = headers.get("X-Real-IP", "").strip()
    if x_real and is_public_ip(x_real):
        return x_real

    # 3. Fallback: check first candidate from XFF even if not strictly public (e.g. dev/test)
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first

    if x_real:
        return x_real

    return (remote_addr or "").strip()


def is_public_ip(ip_str: str) -> bool:
    """
    Checks if an IP address string is a valid public, routable IP.
    """
    if not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str.strip())
        return not (ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast)
    except ValueError:
        return False


def resolve_ip_location(ip_str: str) -> dict | None:
    """
    Resolves an IP address to approximate city, region, and country.
    Uses in-memory caching and ip-api.com.
    Returns a dict with {"type": "ip", "city": str, "region": str, "country": str, "lat": float, "lon": float}
    or None if lookup fails or IP is invalid.
    """
    clean_ip = (ip_str or "").strip()
    if not clean_ip:
        return None

    # Handle private/local IPs gracefully without calling external network
    if not is_public_ip(clean_ip):
        return {
            "type": "ip",
            "city": "Red local / Interna",
            "region": "",
            "country": "",
            "lat": None,
            "lon": None,
            "is_local": True
        }

    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(clean_ip)
        if cached and (now - cached["ts"] < _CACHE_TTL):
            return cached["data"]

    # Perform external lookup with short timeout
    try:
        url = f"http://ip-api.com/json/{clean_ip}?fields=status,message,country,regionName,city,lat,lon"
        req = urllib.request.Request(url, headers={"User-Agent": "BodaApp/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("status") == "success":
            result = {
                "type": "ip",
                "city": data.get("city", "").strip(),
                "region": data.get("regionName", "").strip(),
                "country": data.get("country", "").strip(),
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "is_local": False
            }
            with _CACHE_LOCK:
                _CACHE[clean_ip] = {"ts": now, "data": result}
            return result
        else:
            logger.debug(f"[GEO] ip-api returned non-success for {clean_ip}: {data.get('message')}")
    except Exception as e:
        logger.debug(f"[GEO] Error querying IP location for {clean_ip}: {e}")

    return None


def format_location_summary(location_info: dict | None) -> str | None:
    """
    Formats a concise human-readable location summary for Telegram messages.
    - If GPS: returns link to Google Maps (with precision radius).
    - If IP: returns only the city and region/country.
    """
    if not location_info or not isinstance(location_info, dict):
        return None

    loc_type = location_info.get("type")

    # Precise GPS
    if loc_type == "gps":
        lat = location_info.get("lat")
        lon = location_info.get("lon")
        if lat is not None and lon is not None:
            acc = location_info.get("accuracy")
            acc_str = f" (±{acc}m)" if acc else ""
            maps_url = f"https://maps.google.com/?q={lat},{lon}"
            return f'📍 <b>Ubicación:</b> <a href="{maps_url}">Ver en Google Maps</a>{acc_str}'

    # Approximate IP
    if loc_type == "ip":
        city = (location_info.get("city") or "").strip()
        region = (location_info.get("region") or "").strip()
        country = (location_info.get("country") or "").strip()

        parts = []
        if city and city != "Red local / Interna":
            parts.append(city)
        if region and region != city:
            parts.append(region)
        if country and country not in parts:
            parts.append(country)

        if parts:
            city_str = ", ".join(parts)
            return f"🏙️ <b>Ciudad:</b> {city_str}"
        elif location_info.get("is_local"):
            return "🏙️ <b>Ciudad:</b> Red local / Interna"

    return None
