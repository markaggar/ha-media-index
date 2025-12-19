"""Geocoding service for converting GPS coordinates to location names.

Nominatim Usage Policy Compliance:
- Rate limiting: 1 request/second maximum (enforced)
- User-Agent: Includes contact/project URL (required)
- Caching: All results cached permanently in database (required)
- Attribution: OSM/ODbL attribution displayed in sensor attributes (required)
- Acceptable use: Photos are user-generated content (not bulk/systematic queries)
  Users are geocoding their own photo libraries, not harvesting grid data.

See: https://operations.osmfoundation.org/policies/nominatim/
"""
import asyncio
import logging
from typing import Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Nominatim API endpoint (OpenStreetMap)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

# Rate limiting: 1 request per second (Nominatim requirement)
RATE_LIMIT_DELAY = 1.0

# Coordinate precision for caching (0.001° ≈ 111m)
COORDINATE_PRECISION = 3


class GeocodeService:
    """Service for geocoding GPS coordinates to location names."""

    def __init__(self, hass, use_native_language=False):
        """Initialize the geocoding service.
        
        Args:
            hass: Home Assistant instance
            use_native_language: If True, request native language names from Nominatim (location's local language).
                               If False (default), use Home Assistant's configured language setting.
        """
        self.hass = hass
        self.use_native_language = use_native_language
        self._last_request_time = 0
        self._session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def _round_coordinate(self, coord: float) -> float:
        """Round coordinate to cache precision."""
        return round(coord, COORDINATE_PRECISION)

    async def _rate_limit(self):
        """Enforce rate limiting (1 request per second)."""
        current_time = asyncio.get_event_loop().time()
        time_since_last = current_time - self._last_request_time
        
        if time_since_last < RATE_LIMIT_DELAY:
            delay = RATE_LIMIT_DELAY - time_since_last
            _LOGGER.debug(f"Rate limiting: waiting {delay:.2f}s")
            await asyncio.sleep(delay)
        
        self._last_request_time = asyncio.get_event_loop().time()

    async def reverse_geocode(
        self, 
        latitude: float, 
        longitude: float,
        max_retries: int = 3
    ) -> Optional[dict]:
        """
        Convert GPS coordinates to location information.
        
        Args:
            latitude: Latitude in decimal degrees
            longitude: Longitude in decimal degrees
            max_retries: Maximum number of retry attempts
            
        Returns:
            Dictionary with location data or None if failed:
            {
                'location_name': str,  # Place name or address
                'location_city': str,  # City name
                'location_state': str,  # State/province/region
                'location_country': str  # Country name
            }
        """
        # Round coordinates for consistency
        lat = self._round_coordinate(latitude)
        lon = self._round_coordinate(longitude)
        
        _LOGGER.debug(f"Geocoding ({lat}, {lon})")
        
        session = await self._get_session()
        
        for attempt in range(max_retries):
            try:
                # Enforce rate limiting
                await self._rate_limit()
                
                # Make request to Nominatim
                params = {
                    'lat': lat,
                    'lon': lon,
                    'format': 'json',
                    'addressdetails': 1,
                    'zoom': 18  # High detail level
                }
                
                # Nominatim requires valid User-Agent with contact info
                # See: https://operations.osmfoundation.org/policies/nominatim/
                headers = {
                    'User-Agent': 'HomeAssistant-MediaIndex/1.0 (+https://github.com/markaggar/ha-media-index)'
                }
                
                # Request location names based on configuration:
                # - use_native_language=True: Use location's local language (no Accept-Language header)
                # - use_native_language=False: Use Home Assistant instance's configured language (default)
                if not self.use_native_language:
                    # Use Home Assistant's configured language (defaults to 'en' if not set)
                    ha_language = getattr(self.hass.config, 'language', 'en')
                    headers['Accept-Language'] = ha_language
                
                async with session.get(
                    NOMINATIM_URL, 
                    params=params, 
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return self._parse_nominatim_response(data)
                    elif response.status == 429:
                        # Rate limit exceeded
                        wait_time = 2 ** attempt  # Exponential backoff
                        _LOGGER.warning(
                            f"Nominatim rate limit exceeded, waiting {wait_time}s"
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        _LOGGER.warning(
                            f"Nominatim returned status {response.status}"
                        )
                        return None
                        
            except asyncio.TimeoutError:
                _LOGGER.warning(f"Geocoding timeout (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return None
                
            except Exception as e:
                _LOGGER.error(f"Geocoding error: {e}")
                return None
        
        return None

    def _parse_nominatim_response(self, data: dict) -> dict:
        """Parse Nominatim response to extract location information."""
        address = data.get('address', {})
        
        # Extract location name (most specific place)
        location_name = (
            address.get('amenity') or
            address.get('building') or
            address.get('tourism') or
            address.get('leisure') or
            address.get('suburb') or
            address.get('neighbourhood') or
            address.get('hamlet') or
            address.get('village') or
            address.get('town') or
            address.get('city') or
            data.get('display_name', '').split(',')[0]
        )
        
        # Extract city (prefer city, town, village in that order)
        location_city = (
            address.get('city') or
            address.get('town') or
            address.get('village') or
            address.get('municipality') or
            ''
        )
        
        # Extract state/province
        location_state = (
            address.get('state') or
            address.get('province') or
            address.get('region') or
            ''
        )
        
        # Extract country
        location_country = address.get('country', '')
        
        result = {
            'location_name': location_name.strip() if location_name else '',
            'location_city': location_city.strip() if location_city else '',
            'location_state': location_state.strip() if location_state else '',
            'location_country': location_country.strip() if location_country else ''
        }
        
        _LOGGER.debug(f"Geocoded to: {result}")
        return result
