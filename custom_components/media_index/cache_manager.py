"""SQLite cache manager for media file indexing."""
import aiosqlite
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

_LOGGER = logging.getLogger(__name__)

class CacheManager:
    """Manage SQLite cache for media files."""
    
    def __init__(self, db_path: str):
        """Initialize cache manager.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        
        # Geocoding stats batching
        self._geocode_stats_cache_hits = 0
        self._geocode_stats_cache_misses = 0
        self._geocode_stats_counter = 0
        
        _LOGGER.info("CacheManager initialized with database: %s", db_path)
    
    async def async_setup(self) -> bool:
        """Set up database connection and schema.
        
        Returns:
            True if setup successful
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            # Connect to database
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row
            
            # CRITICAL: Enable foreign key constraints
            # Without this, ON DELETE CASCADE doesn't work and orphaned exif_data accumulates!
            await self._db.execute("PRAGMA foreign_keys = ON")
            
            # Create schema
            await self._create_schema()
            
            # Run one-time migration to sanitize Unicode location names
            # DISABLED - sanitization may not be needed, see CHANGELOG
            # await self._sanitize_location_names()
            
            _LOGGER.info("Cache database initialized successfully")
            return True
            
        except Exception as e:
            _LOGGER.error("Failed to initialize cache database: %s", e)
            return False
    
    async def _create_schema(self) -> None:
        """Create database schema if it doesn't exist."""
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS media_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                folder TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER,
                modified_time INTEGER NOT NULL,
                created_time INTEGER,
                duration REAL,
                width INTEGER,
                height INTEGER,
                orientation TEXT,
                last_scanned INTEGER NOT NULL,
                is_favorited INTEGER DEFAULT 0,
                rating INTEGER DEFAULT 0,
                rated_at INTEGER
            )
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_folder ON media_files(folder)
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_modified ON media_files(modified_time)
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_type ON media_files(file_type)
        """)
        
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS exif_data (
                file_id INTEGER PRIMARY KEY,
                camera_make TEXT,
                camera_model TEXT,
                date_taken INTEGER,
                latitude REAL,
                longitude REAL,
                altitude REAL,
                location_name TEXT,
                location_city TEXT,
                location_state TEXT,
                location_country TEXT,
                rating INTEGER,
                is_favorited INTEGER DEFAULT 0,
                iso INTEGER,
                aperture REAL,
                shutter_speed TEXT,
                focal_length REAL,
                focal_length_35mm INTEGER,
                exposure_compensation TEXT,
                metering_mode TEXT,
                white_balance TEXT,
                flash TEXT,
                FOREIGN KEY (file_id) REFERENCES media_files(id) ON DELETE CASCADE
            )
        """)
        
        # Create indexes for commonly queried EXIF fields
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exif_date_taken ON exif_data(date_taken)
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exif_location_city ON exif_data(location_city)
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exif_location_country ON exif_data(location_country)
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exif_location_name ON exif_data(location_name)
        """)
        
        # Composite index for location + date queries (e.g., "photos from Paris in 2023")
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exif_location_date 
            ON exif_data(location_city, date_taken)
        """)
        
        # Index for GPS coordinate queries (nearby photos)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exif_gps_coords 
            ON exif_data(latitude, longitude)
        """)
        
        # Index for favorites filtering
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exif_favorited ON exif_data(is_favorited)
        """)
        
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS geocode_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                precision_level INTEGER NOT NULL,
                location_name TEXT,
                location_city TEXT,
                location_state TEXT,
                location_country TEXT,
                cached_at INTEGER NOT NULL,
                UNIQUE(latitude, longitude, precision_level)
            )
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_geocode_coords 
            ON geocode_cache(latitude, longitude, precision_level)
        """)
        
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder_path TEXT NOT NULL,
                scan_type TEXT NOT NULL,
                start_time INTEGER NOT NULL,
                end_time INTEGER,
                files_added INTEGER DEFAULT 0,
                files_updated INTEGER DEFAULT 0,
                files_removed INTEGER DEFAULT 0,
                status TEXT
            )
        """)
        
        # Move history table for tracking file moves (e.g., to _Edit folder)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS move_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_path TEXT NOT NULL,
                new_path TEXT NOT NULL,
                moved_at INTEGER NOT NULL,
                move_reason TEXT,
                restored INTEGER DEFAULT 0,
                restored_at INTEGER
            )
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_move_history_new_path 
            ON move_history(new_path)
        """)
        
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_move_history_restored 
            ON move_history(restored)
        """)
        
        # Geocode stats table for tracking cache hit rate
        # Uses singleton pattern: CHECK (id = 1) ensures only one row exists for global statistics
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS geocode_stats (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cache_hits INTEGER DEFAULT 0,
                cache_misses INTEGER DEFAULT 0
            )
        """)
        
        # Initialize stats row if it doesn't exist
        await self._db.execute("""
            INSERT OR IGNORE INTO geocode_stats (id, cache_hits, cache_misses)
            VALUES (1, 0, 0)
        """)
        
        await self._db.commit()
        _LOGGER.debug("Database schema created/verified")
        
        # Run migrations for existing databases
        await self._run_migrations()
    
    async def _run_migrations(self) -> None:
        """Run database migrations for schema updates."""
        # Check if new columns exist in exif_data table
        async with self._db.execute("PRAGMA table_info(exif_data)") as cursor:
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
        
        # Add new EXIF columns if they don't exist
        new_columns = {
            'altitude': 'REAL',
            'focal_length_35mm': 'INTEGER',
            'exposure_compensation': 'TEXT',
            'metering_mode': 'TEXT',
            'white_balance': 'TEXT',
            'burst_favorites': 'TEXT',
            'burst_count': 'INTEGER'
        }
        
        # Validate against whitelist to prevent SQL injection
        allowed_col_names = set(new_columns.keys())
        allowed_col_types = {"REAL", "INTEGER", "TEXT"}
        
        for col_name, col_type in new_columns.items():
            if col_name not in column_names:
                # Additional safety check
                if col_name not in allowed_col_names or col_type not in allowed_col_types:
                    _LOGGER.error("Attempted to add invalid column or type: %s %s", col_name, col_type)
                    continue
                _LOGGER.info("Adding column '%s' to exif_data table", col_name)
                await self._db.execute(f"ALTER TABLE exif_data ADD COLUMN {col_name} {col_type}")
        
        await self._db.commit()
        _LOGGER.debug("Database migrations completed")
    
    async def _sanitize_location_names(self) -> None:
        """One-time migration to sanitize Unicode location names to ASCII.
        
        DISABLED - May not be needed. Real issue was pymediainfo exception logging.
        Keeping code for reference in case future testing reveals it's necessary.
        
        Converts existing geocoded location names to ASCII-safe equivalents
        to prevent UnicodeEncodeError in Python 3.13+.
        """
        return  # Migration disabled
        
        # try:
        #     from .const import sanitize_unicode_to_ascii
        #     
        #     # Get all rows with location data
        #     cursor = await self._db.execute("""
        #         SELECT file_id, location_city, location_state, location_country
        #         FROM exif_data
        #         WHERE location_city IS NOT NULL 
        #            OR location_state IS NOT NULL 
        #            OR location_country IS NOT NULL
        #     """)
        #     rows = await cursor.fetchall()
        #     
        #     if not rows:
        #         return
        #     
        #     # Update each row
        #     updated_count = 0
        #     for row in rows:
        #         file_id = row[0]
        #         city = sanitize_unicode_to_ascii(row[1])
        #         state = sanitize_unicode_to_ascii(row[2])
        #         country = sanitize_unicode_to_ascii(row[3])
        #         
        #         # Only update if something changed
        #         if city != row[1] or state != row[2] or country != row[3]:
        #             await self._db.execute("""
        #                 UPDATE exif_data
        #                 SET location_city = ?,
        #                     location_state = ?,
        #                     location_country = ?
        #                 WHERE file_id = ?
        #             """, (city, state, country, file_id))
        #             updated_count += 1
        #     
        #     if updated_count > 0:
        #         await self._db.commit()
        #         _LOGGER.info(f"Sanitized {updated_count} location name(s) to ASCII (Unicode → ASCII conversion)")
        #     
        # except Exception as e:
        #     _LOGGER.warning(f"Failed to sanitize location names: {e}")
    
    async def get_total_files(self) -> int:
        """Get total number of indexed files.
        
        Returns:
            Total file count
        """
        async with self._db.execute("SELECT COUNT(*) FROM media_files") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def get_total_by_type(self, file_type: str) -> int:
        """Get total files of specific type.
        
        Args:
            file_type: File type (image, video)
            
        Returns:
            Count of files
        """
        async with self._db.execute(
            "SELECT COUNT(*) FROM media_files WHERE file_type = ?",
            (file_type,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def get_total_folders(self) -> int:
        """Get number of unique folders.
        
        Returns:
            Folder count
        """
        async with self._db.execute(
            "SELECT COUNT(DISTINCT folder) FROM media_files"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        total_files = await self.get_total_files()
        total_images = await self.get_total_by_type('image')
        total_videos = await self.get_total_by_type('video')
        total_folders = await self.get_total_folders()
        
        # Get database file size
        cache_size_mb = 0.0
        if os.path.exists(self.db_path):
            cache_size_mb = os.path.getsize(self.db_path) / (1024 * 1024)
        
        # Get files with geocoded location data (location_city indicates geocoding completed)
        async with self._db.execute(
            "SELECT COUNT(*) FROM exif_data WHERE location_city IS NOT NULL AND location_city != ''"
        ) as cursor:
            row = await cursor.fetchone()
            files_with_location = row[0] if row else 0
        
        # Get geocode cache stats
        async with self._db.execute("SELECT COUNT(*) FROM geocode_cache") as cursor:
            row = await cursor.fetchone()
            geocode_cache_entries = row[0] if row else 0
        
        # Get geocode hit rate
        geocode_hit_rate = 0.0
        async with self._db.execute(
            "SELECT cache_hits, cache_misses FROM geocode_stats WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                cache_hits = row[0] or 0
                cache_misses = row[1] or 0
                total_lookups = cache_hits + cache_misses
                if total_lookups > 0:
                    geocode_hit_rate = (cache_hits / total_lookups) * 100
        
        # Get last scan time
        last_scan_time = None
        async with self._db.execute(
            "SELECT MAX(end_time) FROM scan_history WHERE status = 'completed'"
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                last_scan_time = datetime.fromtimestamp(row[0]).isoformat()
        
        return {
            "total_files": total_files,
            "total_images": total_images,
            "total_videos": total_videos,
            "total_folders": total_folders,
            "cache_size_mb": round(cache_size_mb, 2),
            "files_with_location": files_with_location,
            "geocode_cache_entries": geocode_cache_entries,
            "geocode_hit_rate": round(geocode_hit_rate, 1),
            "last_scan_time": last_scan_time,
        }
    
    async def add_file(self, file_data: Dict[str, Any]) -> int:
        """Add file to cache.
        
        Args:
            file_data: File metadata dictionary
            
        Returns:
            File ID
        """
        # Check if file exists and if modified_time has changed
        # Only update last_scanned if file is new OR modified_time changed
        current_time = int(datetime.now().timestamp())
        
        async with self._db.execute(
            "SELECT modified_time, last_scanned FROM media_files WHERE path = ?",
            (file_data['path'],)
        ) as cursor:
            existing_row = await cursor.fetchone()
        
        # Determine last_scanned value:
        # - If file doesn't exist: use current_time (new file)
        # - If modified_time changed: use current_time (file was modified)
        # - If modified_time unchanged: preserve existing last_scanned (file hasn't changed)
        if existing_row is None:
            # New file - use current timestamp
            last_scanned_value = current_time
        elif existing_row[0] != file_data['modified_time']:
            # File modified - use current timestamp
            last_scanned_value = current_time
        else:
            # File unchanged - preserve existing last_scanned
            last_scanned_value = existing_row[1]
        
        # Use INSERT ... ON CONFLICT DO UPDATE to preserve file_id and foreign key relationships
        # This prevents orphaning exif_data when re-scanning existing files
        await self._db.execute("""
            INSERT INTO media_files 
            (path, filename, folder, file_type, file_size, modified_time, 
             created_time, duration, last_scanned, width, height, orientation,
             is_favorited, rating, rated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                filename = excluded.filename,
                folder = excluded.folder,
                file_type = excluded.file_type,
                file_size = excluded.file_size,
                modified_time = excluded.modified_time,
                created_time = excluded.created_time,
                duration = excluded.duration,
                last_scanned = excluded.last_scanned,
                width = excluded.width,
                height = excluded.height,
                orientation = excluded.orientation,
                is_favorited = COALESCE(NULLIF(excluded.is_favorited, 0), media_files.is_favorited),
                rating = COALESCE(NULLIF(excluded.rating, 0), media_files.rating),
                rated_at = COALESCE(excluded.rated_at, media_files.rated_at)
        """, (
            file_data['path'],
            file_data['filename'],
            file_data['folder'],
            file_data['file_type'],
            file_data.get('file_size'),
            file_data['modified_time'],
            file_data.get('created_time'),
            file_data.get('duration'),
            last_scanned_value,
            file_data.get('width'),
            file_data.get('height'),
            file_data.get('orientation'),
            file_data.get('is_favorited', 0),
            file_data.get('rating', 0),
            file_data.get('rated_at'),
        ))
        
        await self._db.commit()
        
        # Get the file ID
        async with self._db.execute(
            "SELECT id FROM media_files WHERE path = ?",
            (file_data['path'],)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def add_exif_data(self, file_id: int, exif_data: Dict[str, Any]) -> None:
        """Add or update EXIF data for a file.
        
        Preserves existing geocoded location data to avoid re-geocoding on every scan.
        
        Args:
            file_id: ID of the file in media_files table
            exif_data: Dictionary with EXIF metadata
        """
        # Skip if no EXIF data provided
        if not exif_data:
            return
        
        # Check if EXIF data already exists - preserve geocoded location and favorite data
        existing_data = None
        async with self._db.execute("""
            SELECT location_name, location_city, location_state, location_country,
                   rating, is_favorited
            FROM exif_data
            WHERE file_id = ?
        """, (file_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                existing_data = {
                    'location_name': row[0],
                    'location_city': row[1],
                    'location_state': row[2],
                    'location_country': row[3],
                    'rating': row[4],
                    'is_favorited': row[5]
                }
        
        await self._db.execute("""
            INSERT OR REPLACE INTO exif_data 
            (file_id, camera_make, camera_model, date_taken, latitude, longitude, altitude,
             location_name, location_city, location_state, location_country,
             rating, is_favorited,
             iso, aperture, shutter_speed, focal_length, focal_length_35mm,
             exposure_compensation, metering_mode, white_balance, flash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            file_id,
            exif_data.get('camera_make'),
            exif_data.get('camera_model'),
            exif_data.get('date_taken'),
            exif_data.get('latitude'),
            exif_data.get('longitude'),
            exif_data.get('altitude'),
            # Preserve existing geocoded location if available, otherwise use None
            existing_data['location_name'] if existing_data and existing_data.get('location_city') else None,
            existing_data['location_city'] if existing_data and existing_data.get('location_city') else None,
            existing_data['location_state'] if existing_data and existing_data.get('location_city') else None,
            existing_data['location_country'] if existing_data and existing_data.get('location_city') else None,
            # Use rating from EXIF if present, otherwise preserve existing
            exif_data.get('rating') if exif_data.get('rating') is not None else (existing_data.get('rating') if existing_data else None),
            # Use is_favorited from EXIF if present, otherwise preserve existing
            exif_data.get('is_favorited') if exif_data.get('is_favorited') is not None else (existing_data.get('is_favorited') if existing_data else 0),
            exif_data.get('iso'),
            exif_data.get('aperture'),
            exif_data.get('shutter_speed'),
            exif_data.get('focal_length'),
            exif_data.get('focal_length_35mm'),
            exif_data.get('exposure_compensation'),
            exif_data.get('metering_mode'),
            exif_data.get('white_balance'),
            exif_data.get('flash'),
        ))
        
        await self._db.commit()
    
    async def has_geocoded_location(self, file_id: int) -> bool:
        """Check if a file already has geocoded location data.
        
        Args:
            file_id: ID of the file in media_files table
            
        Returns:
            True if location_city is populated, False otherwise
        """
        async with self._db.execute("""
            SELECT location_city FROM exif_data WHERE file_id = ?
        """, (file_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None and row[0] is not None
    
    async def get_geocode_cache(self, latitude: float, longitude: float) -> Optional[Dict[str, str]]:
        """Get cached geocoding data for coordinates.
        
        Args:
            latitude: Latitude rounded to 3 decimals
            longitude: Longitude rounded to 3 decimals
            
        Returns:
            Dictionary with location data or None if not cached
        """
        from .const import GEOCODE_STATS_BATCH_SIZE
        
        async with self._db.execute("""
            SELECT location_name, location_city, location_state, location_country
            FROM geocode_cache
            WHERE latitude = ? AND longitude = ? AND precision_level = ?
        """, (round(latitude, 3), round(longitude, 3), 3)) as cursor:
            row = await cursor.fetchone()
            if row:
                # Increment in-memory cache hit counter
                self._geocode_stats_cache_hits += 1
                self._geocode_stats_counter += 1
                
                # Flush to database every GEOCODE_STATS_BATCH_SIZE lookups
                if self._geocode_stats_counter >= GEOCODE_STATS_BATCH_SIZE:
                    await self._flush_geocode_stats()
                
                return {
                    'location_name': row[0],
                    'location_city': row[1],
                    'location_state': row[2],
                    'location_country': row[3]
                }
            else:
                # Increment in-memory cache miss counter
                self._geocode_stats_cache_misses += 1
                self._geocode_stats_counter += 1
                
                # Flush to database every GEOCODE_STATS_BATCH_SIZE lookups
                if self._geocode_stats_counter >= GEOCODE_STATS_BATCH_SIZE:
                    await self._flush_geocode_stats()
                    
            return None
    
    async def add_geocode_cache(
        self, 
        latitude: float, 
        longitude: float,
        location_data: Dict[str, str]
    ) -> None:
        """Cache geocoding data for coordinates.
        
        Args:
            latitude: Latitude in decimal degrees
            longitude: Longitude in decimal degrees
            location_data: Dictionary with location_name, location_city, location_state, location_country
        """
        await self._db.execute("""
            INSERT OR REPLACE INTO geocode_cache
            (latitude, longitude, precision_level, location_name, location_city, location_state, location_country, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            round(latitude, 3),
            round(longitude, 3),
            3,  # precision level (3 decimal places)
            location_data.get('location_name', ''),
            location_data.get('location_city', ''),
            location_data.get('location_state', ''),
            location_data.get('location_country', ''),
            int(datetime.now().timestamp())
        ))
        
        await self._db.commit()
    
    async def _flush_geocode_stats(self) -> None:
        """Flush in-memory geocoding stats counters to database.
        
        Called automatically every GEOCODE_STATS_BATCH_SIZE lookups
        or manually when scan completes.
        """
        if self._geocode_stats_cache_hits > 0 or self._geocode_stats_cache_misses > 0:
            await self._db.execute("""
                UPDATE geocode_stats 
                SET cache_hits = cache_hits + ?,
                    cache_misses = cache_misses + ?
                WHERE id = 1
            """, (self._geocode_stats_cache_hits, self._geocode_stats_cache_misses))
            await self._db.commit()
            
            _LOGGER.debug(
                "Flushed geocoding stats: +%d hits, +%d misses",
                self._geocode_stats_cache_hits,
                self._geocode_stats_cache_misses
            )
            
            # Reset counters
            self._geocode_stats_cache_hits = 0
            self._geocode_stats_cache_misses = 0
            self._geocode_stats_counter = 0
    
    async def update_exif_location(
        self,
        file_id: int,
        location_data: Dict[str, str]
    ) -> None:
        """Update location fields in EXIF data.
        
        Args:
            file_id: ID of the file
            location_data: Dictionary with location_name, location_city, location_state, location_country
        """
        await self._db.execute("""
            UPDATE exif_data
            SET location_name = ?, location_city = ?, location_state = ?, location_country = ?
            WHERE file_id = ?
        """, (
            location_data.get('location_name', ''),
            location_data.get('location_city', ''),
            location_data.get('location_state', ''),
            location_data.get('location_country', ''),
            file_id
        ))
        
        await self._db.commit()
    
    async def remove_file(self, file_path: str) -> bool:
        """Remove a file from the cache.
        
        Args:
            file_path: Full path to file
            
        Returns:
            True if file was removed, False otherwise
        """
        try:
            # Remove from media_files table
            await self._db.execute(
                "DELETE FROM media_files WHERE path = ?",
                (file_path,)
            )
            
            await self._db.commit()
            return True
        except Exception as err:
            _LOGGER.error("Failed to remove file %s from cache: %s", file_path, err)
            return False
    
    async def record_scan(self, folder_path: str, scan_type: str) -> int:
        """Record start of scan.
        
        Args:
            folder_path: Path being scanned
            scan_type: Type of scan (full, incremental)
            
        Returns:
            Scan history ID
        """
        await self._db.execute("""
            INSERT INTO scan_history 
            (folder_path, scan_type, start_time, status)
            VALUES (?, ?, ?, 'running')
        """, (folder_path, scan_type, int(datetime.now().timestamp())))
        
        await self._db.commit()
        
        async with self._db.execute(
            "SELECT last_insert_rowid()"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def update_scan(self, scan_id: int, files_added: int = 0, 
                         files_updated: int = 0, status: str = 'completed') -> None:
        """Update scan record.
        
        Args:
            scan_id: Scan history ID
            files_added: Number of files added
            files_updated: Number of files updated
            status: Final status
        """
        await self._db.execute("""
            UPDATE scan_history 
            SET end_time = ?, files_added = ?, files_updated = ?, status = ?
            WHERE id = ?
        """, (
            int(datetime.now().timestamp()),
            files_added,
            files_updated,
            status,
            scan_id
        ))
        
        await self._db.commit()
    
    async def get_random_files(
        self,
        count: int = 10,
        folder: str | None = None,
        recursive: bool = True,
        file_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        timestamp_from: int | None = None,
        timestamp_to: int | None = None,
        anniversary_month: str | None = None,
        anniversary_day: str | None = None,
        anniversary_window_days: int = 0,
        favorites_only: bool = False,
        priority_new_files: bool = False,
        new_files_threshold_seconds: int = 3600
    ) -> list[dict]:
        """Get random media files with optional filters and EXIF data.
        
        Includes geocoding status to enable progressive on-demand geocoding.
        If priority_new_files=True, prioritizes recently scanned files.
        
        Args:
            count: Number of random files to return
            folder: Filter by folder path (supports wildcards with %)
            recursive: If False, only match exact folder (no subfolders)
            file_type: Filter by file type ('image' or 'video')
            date_from: Filter by date >= this value (YYYY-MM-DD). Uses EXIF date_taken if available, falls back to created_time.
            date_to: Filter by date <= this value (YYYY-MM-DD). Uses EXIF date_taken if available, falls back to created_time.
            timestamp_from: Filter by timestamp >= this value (Unix timestamp in seconds). Takes precedence over date_from.
            timestamp_to: Filter by timestamp <= this value (Unix timestamp in seconds). Takes precedence over date_to.
            anniversary_month: Filter by month (1-12) or "*" for any month across years
            anniversary_day: Filter by day (1-31) or "*" for any day across years
            anniversary_window_days: Expand anniversary match by ±N days (default 0)
            favorites_only: If True, only return files marked as favorites
            priority_new_files: If True, prioritize recently scanned files
            new_files_threshold_seconds: Threshold in seconds for "new" files (default 1 hour)
            
        Returns:
            List of file records with metadata and EXIF data including:
            - has_coordinates: bool (GPS data exists)
            - is_geocoded: bool (location_city populated)
            - latitude, longitude: float (if has_coordinates)
            - location_name, location_city, location_country: str (if is_geocoded)
            - date_taken: timestamp (if available)
        """
        import time
        
        if priority_new_files:
            # Priority queue mode: Get new files first, then fill with random
            current_time = int(time.time())
            threshold_time = current_time - new_files_threshold_seconds
            
            # Query 1: Get newly scanned files (last_scanned > threshold)
            new_files_query = """
                SELECT 
                    m.*,
                    e.date_taken,
                    e.latitude,
                    e.longitude,
                    e.location_name,
                    e.location_city,
                    e.location_state,
                    e.location_country,
                    e.is_favorited,
                    e.camera_make,
                    e.camera_model
                FROM media_files m
                LEFT JOIN exif_data e ON m.id = e.file_id
                WHERE m.last_scanned > ?
                  AND m.folder NOT LIKE '%/_Junk%'
                  AND m.folder NOT LIKE '%/_Edit%'
            """
            params = [threshold_time]
            
            if folder:
                if recursive:
                    # Recursive: match folder and all subfolders
                    new_files_query += " AND LOWER(m.folder) LIKE LOWER(?)"
                    params.append(f"{folder}%")
                else:
                    # Non-recursive: exact folder match only
                    new_files_query += " AND LOWER(m.folder) = LOWER(?)"
                    params.append(folder)
            
            if file_type:
                new_files_query += " AND m.file_type = ?"
                params.append(file_type.lower())
            
            if favorites_only:
                new_files_query += " AND e.is_favorited = 1"
            
            # Timestamp filtering (takes precedence over date filtering)
            if timestamp_from is not None:
                new_files_query += " AND COALESCE(e.date_taken, m.created_time) >= ?"
                params.append(timestamp_from)
            elif date_from is not None:
                # Validate date_from is a valid date string using datetime.strptime
                try:
                    date_from_str = str(date_from) if not isinstance(date_from, str) else date_from
                    # Proper validation with datetime.strptime - prevents invalid dates like 2024-13-45
                    dt = datetime.strptime(date_from_str, "%Y-%m-%d")
                    # Convert to Unix timestamp using server local time (matches how EXIF timestamps are stored)
                    timestamp = int(dt.timestamp())
                    new_files_query += " AND COALESCE(e.date_taken, m.created_time) >= ?"
                    params.append(timestamp)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning("Invalid date_from parameter: %s - %s", date_from, e)
            
            if timestamp_to is not None:
                new_files_query += " AND COALESCE(e.date_taken, m.created_time) <= ?"
                params.append(timestamp_to)
            elif date_to is not None:
                # Validate date_to is a valid date string using datetime.strptime
                try:
                    date_to_str = str(date_to) if not isinstance(date_to, str) else date_to
                    # Proper validation with datetime.strptime - prevents invalid dates like 2024-13-45
                    dt = datetime.strptime(date_to_str, "%Y-%m-%d")
                    # Convert to Unix timestamp using server local time (end of local day = start of next day minus 1)
                    timestamp = int((dt + timedelta(days=1)).timestamp()) - 1
                    new_files_query += " AND COALESCE(e.date_taken, m.created_time) <= ?"
                    params.append(timestamp)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning("Invalid date_to parameter: %s - %s", date_to, e)
            
            # Anniversary filtering: Match month/day across years (supports wildcards)
            if anniversary_month is not None or anniversary_day is not None:
                ann_conditions = []
                
                # Apply window to day if specified (e.g., day 7 ±3 = days 4-10)
                if anniversary_day and anniversary_day != "*":
                    try:
                        day_int = int(anniversary_day)
                        if anniversary_window_days > 0:
                            # Generate day range with window
                            day_min = max(1, day_int - anniversary_window_days)
                            day_max = min(31, day_int + anniversary_window_days)
                            ann_conditions.append("CAST(strftime('%d', COALESCE(e.date_taken, m.created_time), 'unixepoch', 'localtime') AS INTEGER) BETWEEN ? AND ?")
                            params.extend([day_min, day_max])
                        else:
                            # Exact day match
                            ann_conditions.append("CAST(strftime('%d', COALESCE(e.date_taken, m.created_time), 'unixepoch', 'localtime') AS INTEGER) = ?")
                            params.append(day_int)
                    except ValueError:
                        _LOGGER.warning("Invalid anniversary_day parameter: %s", anniversary_day)
                # else: wildcard "*" means any day - no condition added
                
                # Month matching (no window for month)
                if anniversary_month and anniversary_month != "*":
                    try:
                        month_int = int(anniversary_month)
                        ann_conditions.append("CAST(strftime('%m', COALESCE(e.date_taken, m.created_time), 'unixepoch', 'localtime') AS INTEGER) = ?")
                        params.append(month_int)
                    except ValueError:
                        _LOGGER.warning("Invalid anniversary_month parameter: %s", anniversary_month)
                # else: wildcard "*" means any month - no condition added
                
                if ann_conditions:
                    new_files_query += " AND (" + " AND ".join(ann_conditions) + ")"
            
            # V5 IMPROVEMENT: Get ALL recent files, then randomly sample
            # This ensures even distribution - all recent files have equal chance
            # Fixes "last 20" problem where only first 20 recent files were returned
            new_files_query += " ORDER BY m.last_scanned DESC"
            # Note: No LIMIT here - we get all recent files, then sample below
            
            # Debug logging removed to prevent excessive logs during slideshow
            
            async with self._db.execute(new_files_query, tuple(params)) as cursor:
                new_files_rows = await cursor.fetchall()
            
            all_new_files = [dict(row) for row in new_files_rows]
            # Debug: Found X total recent files (logging removed)
            
            # Randomly sample from recent files (up to count requested)
            import random
            if len(all_new_files) > count:
                new_files = random.sample(all_new_files, count)
                # Debug: Randomly sampled X from Y recent files (logging removed)
            else:
                new_files = all_new_files
            
            # Query 2: Fill remaining slots with random non-recent files
            remaining = count - len(new_files)
            if remaining > 0:
                exclude_ids = [f['id'] for f in new_files]
                random_files = await self._get_random_excluding(
                    count=remaining,
                    exclude_ids=exclude_ids,
                    folder=folder,
                    recursive=recursive,
                    file_type=file_type,
                    date_from=date_from,
                    date_to=date_to,
                    timestamp_from=timestamp_from,
                    timestamp_to=timestamp_to,
                    anniversary_month=anniversary_month,
                    anniversary_day=anniversary_day,
                    anniversary_window_days=anniversary_window_days,
                    favorites_only=favorites_only
                )
                result = new_files + random_files
            else:
                result = new_files[:count]
            
            # Add geocoding status flags
            for item in result:
                item['has_coordinates'] = item.get('latitude') is not None and item.get('longitude') is not None
                item['is_geocoded'] = item.get('location_city') is not None
            
            # Debug: Priority queue returned X new files + Y random files (logging removed)
            return result
        
        else:
            # Standard random mode (backward compatible)
            query = """
                SELECT 
                    m.*,
                    e.date_taken,
                    e.latitude,
                    e.longitude,
                    e.location_name,
                    e.location_city,
                    e.location_state,
                    e.location_country,
                    e.is_favorited,
                    e.camera_make,
                    e.camera_model
                FROM media_files m
                LEFT JOIN exif_data e ON m.id = e.file_id
                WHERE 1=1
                    AND m.folder NOT LIKE '%/_Junk%'
                    AND m.folder NOT LIKE '%/_Edit%'
            """
            params = []
            
            if folder:
                # Use case-insensitive matching for folder paths (handles /media/Photo vs /media/photo)
                if recursive:
                    # Recursive: match folder and all subfolders
                    query += " AND LOWER(m.folder) LIKE LOWER(?)"
                    params.append(f"{folder}%")
                else:
                    # Non-recursive: exact folder match only
                    query += " AND LOWER(m.folder) = LOWER(?)"
                    params.append(folder)
            
            if file_type:
                query += " AND m.file_type = ?"
                params.append(file_type.lower())
            
            if favorites_only:
                query += " AND e.is_favorited = 1"
            
            # Timestamp filtering (takes precedence over date filtering)
            if timestamp_from is not None:
                query += " AND COALESCE(e.date_taken, m.created_time) >= ?"
                params.append(timestamp_from)
            elif date_from is not None:
                # Validate date_from is a valid date string using datetime.strptime
                try:
                    date_from_str = str(date_from) if not isinstance(date_from, str) else date_from
                    # Proper validation with datetime.strptime - prevents invalid dates like 2024-13-45
                    dt = datetime.strptime(date_from_str, "%Y-%m-%d")
                    # Convert to Unix timestamp using server local time (matches how EXIF timestamps are stored)
                    timestamp = int(dt.timestamp())
                    query += " AND COALESCE(e.date_taken, m.created_time) >= ?"
                    params.append(timestamp)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning("Invalid date_from parameter: %s - %s", date_from, e)
            
            if timestamp_to is not None:
                query += " AND COALESCE(e.date_taken, m.created_time) <= ?"
                params.append(timestamp_to)
            elif date_to is not None:
                # Validate date_to is a valid date string using datetime.strptime
                try:
                    date_to_str = str(date_to) if not isinstance(date_to, str) else date_to
                    # Proper validation with datetime.strptime - prevents invalid dates like 2024-13-45
                    dt = datetime.strptime(date_to_str, "%Y-%m-%d")
                    # Convert to Unix timestamp using server local time (end of local day = start of next day minus 1)
                    timestamp = int((dt + timedelta(days=1)).timestamp()) - 1
                    query += " AND COALESCE(e.date_taken, m.created_time) <= ?"
                    params.append(timestamp)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning("Invalid date_to parameter: %s - %s", date_to, e)
            
            # Anniversary filtering: Match month/day across years (supports wildcards)
            if anniversary_month is not None or anniversary_day is not None:
                ann_conditions = []
                
                # Apply window to day if specified (e.g., day 7 ±3 = days 4-10)
                if anniversary_day and anniversary_day != "*":
                    try:
                        day_int = int(anniversary_day)
                        if anniversary_window_days > 0:
                            # Generate day range with window
                            day_min = max(1, day_int - anniversary_window_days)
                            day_max = min(31, day_int + anniversary_window_days)
                            ann_conditions.append("CAST(strftime('%d', COALESCE(e.date_taken, m.created_time), 'unixepoch', 'localtime') AS INTEGER) BETWEEN ? AND ?")
                            params.extend([day_min, day_max])
                        else:
                            # Exact day match
                            ann_conditions.append("CAST(strftime('%d', COALESCE(e.date_taken, m.created_time), 'unixepoch', 'localtime') AS INTEGER) = ?")
                            params.append(day_int)
                    except ValueError:
                        _LOGGER.warning("Invalid anniversary_day parameter: %s", anniversary_day)
                # else: wildcard "*" means any day - no condition added
                
                # Month matching (no window for month)
                if anniversary_month and anniversary_month != "*":
                    try:
                        month_int = int(anniversary_month)
                        ann_conditions.append("CAST(strftime('%m', COALESCE(e.date_taken, m.created_time), 'unixepoch', 'localtime') AS INTEGER) = ?")
                        params.append(month_int)
                    except ValueError:
                        _LOGGER.warning("Invalid anniversary_month parameter: %s", anniversary_month)
                # else: wildcard "*" means any month - no condition added
                
                if ann_conditions:
                    query += " AND (" + " AND ".join(ann_conditions) + ")"
            
            query += " ORDER BY RANDOM() LIMIT ?"
            params.append(int(count))
            
            # Debug logging removed to prevent excessive logs during slideshow
            
            async with self._db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
            
            # Convert rows to dicts and add geocoding status
            result = []
            for row in rows:
                item = dict(row)
                # Add progressive geocoding flags
                item['has_coordinates'] = item.get('latitude') is not None and item.get('longitude') is not None
                item['is_geocoded'] = item.get('location_city') is not None
                result.append(item)
            
            return result
    
    async def _get_random_excluding(
        self,
        count: int,
        exclude_ids: list[int],
        folder: str | None = None,
        recursive: bool = True,
        file_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        timestamp_from: int | None = None,
        timestamp_to: int | None = None,
        anniversary_month: str | None = None,
        anniversary_day: str | None = None,
        anniversary_window_days: int = 0,
        favorites_only: bool = False
    ) -> list[dict]:
        """Get random files excluding specified IDs (helper for priority queue).
        
        Args:
            count: Number of files to return
            exclude_ids: List of file IDs to exclude
            folder: Optional folder filter
            recursive: If False, only match exact folder (no subfolders)
            file_type: Optional file type filter
            date_from: Optional date from filter
            date_to: Optional date to filter
            timestamp_from: Optional timestamp from filter (takes precedence)
            timestamp_to: Optional timestamp to filter (takes precedence)
            anniversary_month: Filter by month (1-12) or "*" for any
            anniversary_day: Filter by day (1-31) or "*" for any
            anniversary_window_days: Expand anniversary match by ±N days
            
        Returns:
            List of random file records excluding specified IDs
        """
        query = """
            SELECT 
                m.*,
                e.date_taken,
                e.latitude,
                e.longitude,
                e.location_name,
                e.location_city,
                e.location_state,
                e.location_country,
                e.is_favorited,
                e.camera_make,
                e.camera_model
            FROM media_files m
            LEFT JOIN exif_data e ON m.id = e.file_id
            WHERE 1=1
              AND m.folder NOT LIKE '%/_Junk%'
              AND m.folder NOT LIKE '%/_Edit%'
        """
        params = []
        
        # Exclude new files already selected
        if exclude_ids:
            # Ensure only integers are used in exclude_ids
            safe_exclude_ids = [int(x) for x in exclude_ids if isinstance(x, int) or (isinstance(x, str) and x.isdigit())]
            if len(safe_exclude_ids) != len(exclude_ids):
                _LOGGER.warning("Some exclude_ids were not integers and have been ignored: %s", set(exclude_ids) - set(safe_exclude_ids))
            if safe_exclude_ids:
                placeholders = ','.join('?' * len(safe_exclude_ids))
                query += f" AND m.id NOT IN ({placeholders})"
                params.extend(safe_exclude_ids)
        
        if folder:
            if recursive:
                # Recursive: match folder and all subfolders
                query += " AND LOWER(m.folder) LIKE LOWER(?)"
                params.append(f"{folder}%")
            else:
                # Non-recursive: exact folder match only
                query += " AND LOWER(m.folder) = LOWER(?)"
                params.append(folder)
        
        if file_type:
            query += " AND m.file_type = ?"
            params.append(file_type.lower())
        
        if favorites_only:
            query += " AND e.is_favorited = 1"
        
        # Timestamp filtering (takes precedence over date filtering)
        if timestamp_from is not None:
            query += " AND COALESCE(e.date_taken, m.created_time) >= ?"
            params.append(timestamp_from)
        elif date_from is not None:
            # Validate date_from is a valid date string
            try:
                date_from_str = str(date_from) if not isinstance(date_from, str) else date_from
                dt = datetime.strptime(date_from_str, "%Y-%m-%d")
                # Convert to Unix timestamp using server local time (matches how EXIF timestamps are stored)
                timestamp = int(dt.timestamp())
                query += " AND COALESCE(e.date_taken, m.created_time) >= ?"
                params.append(timestamp)
            except (ValueError, TypeError) as e:
                _LOGGER.warning("Invalid date_from parameter: %s - %s", date_from, e)
        
        if timestamp_to is not None:
            query += " AND COALESCE(e.date_taken, m.created_time) <= ?"
            params.append(timestamp_to)
        elif date_to is not None:
            # Validate date_to is a valid date string
            try:
                date_to_str = str(date_to) if not isinstance(date_to, str) else date_to
                dt = datetime.strptime(date_to_str, "%Y-%m-%d")
                # Convert to Unix timestamp using server local time (end of local day = start of next day minus 1)
                timestamp = int((dt + timedelta(days=1)).timestamp()) - 1
                query += " AND COALESCE(e.date_taken, m.created_time) <= ?"
                params.append(timestamp)
            except (ValueError, TypeError) as e:
                _LOGGER.warning("Invalid date_to parameter: %s - %s", date_to, e)
        
        # Anniversary filtering: Match month/day across years (supports wildcards)
        if anniversary_month is not None or anniversary_day is not None:
            ann_conditions = []
            
            # Apply window to day if specified
            if anniversary_day and anniversary_day != "*":
                try:
                    day_int = int(anniversary_day)
                    if anniversary_window_days > 0:
                        day_min = max(1, day_int - anniversary_window_days)
                        day_max = min(31, day_int + anniversary_window_days)
                        ann_conditions.append("CAST(strftime('%d', COALESCE(e.date_taken, m.created_time), 'unixepoch', 'localtime') AS INTEGER) BETWEEN ? AND ?")
                        params.extend([day_min, day_max])
                    else:
                        ann_conditions.append("CAST(strftime('%d', COALESCE(e.date_taken, m.created_time), 'unixepoch', 'localtime') AS INTEGER) = ?")
                        params.append(day_int)
                except ValueError:
                    _LOGGER.warning("Invalid anniversary_day parameter: %s", anniversary_day)
            
            # Month matching (no window for month)
            if anniversary_month and anniversary_month != "*":
                try:
                    month_int = int(anniversary_month)
                    ann_conditions.append("CAST(strftime('%m', COALESCE(e.date_taken, m.created_time), 'unixepoch', 'localtime') AS INTEGER) = ?")
                    params.append(month_int)
                except ValueError:
                    _LOGGER.warning("Invalid anniversary_month parameter: %s", anniversary_month)
            
            if ann_conditions:
                query += " AND (" + " AND ".join(ann_conditions) + ")"
        
        query += " ORDER BY RANDOM() LIMIT ?"
        params.append(int(count))
        
        async with self._db.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()
        
        result = []
        for row in rows:
            item = dict(row)
            item['has_coordinates'] = item.get('latitude') is not None and item.get('longitude') is not None
            item['is_geocoded'] = item.get('location_city') is not None
            result.append(item)
        
        return result
    
    async def get_ordered_files(
        self,
        count: int = 50,
        folder: str | None = None,
        recursive: bool = True,
        file_type: str | None = None,
        order_by: str = "date_taken",
        order_direction: str = "desc",
        after_value: str | int | float | None = None,
        after_id: int | None = None,
    ) -> list[dict]:
        """Get ordered media files with configurable sort field and direction.
        
        Recursive mode sorts across ALL files regardless of folder boundaries.
        
        Args:
            count: Maximum number of files to return
            folder: Filter by folder path
            recursive: Include subfolders (sorts across all files when true)
            file_type: Filter by file type ('image' or 'video')
            order_by: Sort field - 'date_taken', 'filename', 'path', 'modified_time'
            order_direction: Sort direction - 'asc' or 'desc'
            after_value: Cursor for pagination - return items AFTER this value
            after_id: Secondary cursor (file ID) for tie-breaking when values are equal
            
        Returns:
            List of ordered file records with metadata
        """
        query = """
            SELECT 
                m.*,
                e.date_taken,
                e.latitude,
                e.longitude,
                e.location_name,
                e.location_city,
                e.location_state,
                e.location_country,
                e.is_favorited,
                e.camera_make,
                e.camera_model
            FROM media_files m
            LEFT JOIN exif_data e ON m.id = e.file_id
            WHERE 1=1
              AND m.folder NOT LIKE '%/_Junk%'
              AND m.folder NOT LIKE '%/_Edit%'
        """
        params = []
        
        if folder:
            if recursive:
                # Include subfolders
                query += " AND LOWER(m.folder) LIKE LOWER(?)"
                params.append(f"{folder}%")
            else:
                # Exact folder match only
                query += " AND LOWER(m.folder) = LOWER(?)"
                params.append(folder)
        
        if file_type:
            query += " AND m.file_type = ?"
            params.append(file_type.lower())
        
        # Use explicit whitelist mapping for sort fields and directions
        allowed_sort_fields = {
            "date_taken": "COALESCE(e.date_taken, m.modified_time)",
            "filename": "m.filename",
            "path": "m.folder || '/' || m.filename",
            "modified_time": "m.modified_time",
        }
        allowed_directions = {
            "asc": "ASC",
            "desc": "DESC",
        }
        sort_field = allowed_sort_fields.get(order_by, "COALESCE(e.date_taken, m.modified_time)")
        direction = allowed_directions.get(order_direction.lower(), "DESC")
        
        # v1.5.10: Compound cursor pagination using (sort_field, id)
        # This handles cases where multiple files have the same date_taken
        if after_value is not None:
            if after_id is not None:
                # Compound cursor: items where (sort_field < after_value) OR (sort_field = after_value AND id < after_id)
                # This ensures we skip items we've already seen even with duplicate sort values
                if direction == "DESC":
                    query += f" AND (({sort_field} < ?) OR ({sort_field} = ? AND m.id < ?))"
                    params.extend([after_value, after_value, after_id])
                else:
                    query += f" AND (({sort_field} > ?) OR ({sort_field} = ? AND m.id > ?))"
                    params.extend([after_value, after_value, after_id])
            else:
                # Fallback to simple cursor if no after_id provided
                if direction == "DESC":
                    query += f" AND {sort_field} < ?"
                else:
                    query += f" AND {sort_field} > ?"
                params.append(after_value)
        
        # Order by sort_field, then by id for stable ordering
        query += f" ORDER BY {sort_field} {direction}, m.id {direction} LIMIT ?"
        params.append(int(count))
        
        # Debug logging removed to prevent excessive logs during slideshow
        
        async with self._db.execute(query, tuple(params)) as cursor:
            rows = await cursor.fetchall()
        
        # Convert rows to dicts and add geocoding status
        result = []
        for row in rows:
            item = dict(row)
            item['has_coordinates'] = item.get('latitude') is not None and item.get('longitude') is not None
            item['is_geocoded'] = item.get('location_city') is not None
            result.append(item)
        
        return result
    
    async def get_file_by_path(self, file_path: str) -> dict | None:
        """Get file metadata by full path.
        
        Args:
            file_path: Full path to the file
            
        Returns:
            File record with metadata, or None if not found
        """
        async with self._db.execute(
            "SELECT * FROM media_files WHERE path = ?",
            (file_path,)
        ) as cursor:
            row = await cursor.fetchone()
        
        if not row:
            return None
        
        # Get base file data
        file_data = dict(row)
        
        # Get EXIF data if available (join via file_id)
        async with self._db.execute(
            """SELECT e.* FROM exif_data e 
               JOIN media_files m ON e.file_id = m.id 
               WHERE m.path = ?""",
            (file_path,)
        ) as cursor:
            exif_row = await cursor.fetchone()
        
        if exif_row:
            file_data['exif'] = dict(exif_row)
        
        return file_data
    
    async def get_burst_photos(
        self,
        reference_path: str,
        time_window_seconds: int = 10,
        prefer_same_location: bool = True,
        location_tolerance_meters: int = 50,
        sort_order: str = "time_asc"
    ) -> list[dict]:
        """Get burst photos taken near the same time as a reference photo.
        
        Args:
            reference_path: Path to the reference photo
            time_window_seconds: Time window in seconds (default: 10)
            prefer_same_location: Prioritize photos at same GPS location (default: True)
            location_tolerance_meters: GPS tolerance in meters (default: 50)
            sort_order: Sort order - 'time_asc' or 'time_desc' (default: time_asc)
            
        Returns:
            List of burst photos with metadata
        """
        # Get reference photo metadata
        reference_file = await self.get_file_by_path(reference_path)
        if not reference_file:
            _LOGGER.error("Reference file not found: %s", reference_path)
            return []
        
        # Extract EXIF data from nested object
        exif_data = reference_file.get('exif', {})
        if not exif_data:
            _LOGGER.error("Reference file has no EXIF data: %s", reference_path)
            return []
        
        reference_date_taken = exif_data.get('date_taken')
        if not reference_date_taken:
            _LOGGER.error("Reference file has no date_taken in EXIF: %s", reference_path)
            return []
        
        reference_latitude = exif_data.get('latitude')
        reference_longitude = exif_data.get('longitude')
        
        _LOGGER.info(
            "Burst detection: ref_date=%s, location_present=%s, window=%ds",
            reference_date_taken, reference_latitude is not None and reference_longitude is not None, time_window_seconds
        )
        
        # Build query
        query = """
            SELECT 
                m.id,
                m.path,
                m.filename,
                m.folder,
                m.file_type,
                m.file_size,
                m.modified_time,
                e.date_taken,
                e.camera_make,
                e.camera_model,
                e.latitude,
                e.longitude,
                e.location_city,
                e.location_state,
                e.location_country,
                e.is_favorited,
                e.rating,
                (e.date_taken - ?) AS seconds_offset
        """
        
        # Add GPS distance calculation if location available
        if reference_latitude and reference_longitude and prefer_same_location:
            query += f""",
                (6371000 * acos(
                    cos(radians(?)) * cos(radians(e.latitude)) *
                    cos(radians(e.longitude) - radians(?)) +
                    sin(radians(?)) * sin(radians(e.latitude))
                )) AS distance_meters
            """
        
        query += """
            FROM media_files m
            JOIN exif_data e ON m.id = e.file_id
            WHERE e.date_taken IS NOT NULL
              AND ABS(e.date_taken - ?) <= ?
        """
        
        # Add location filter if applicable
        if reference_latitude and reference_longitude and prefer_same_location:
            query += f"""
              AND (6371000 * acos(
                  cos(radians(?)) * cos(radians(e.latitude)) *
                  cos(radians(e.longitude) - radians(?)) +
                  sin(radians(?)) * sin(radians(e.latitude))
              )) <= ?
            """
        
        # Add sorting
        if sort_order == "time_desc":
            query += " ORDER BY e.date_taken DESC"
        else:
            query += " ORDER BY e.date_taken ASC"
        
        # Build parameters
        params = [reference_date_taken]
        
        if reference_latitude and reference_longitude and prefer_same_location:
            params.extend([reference_latitude, reference_longitude, reference_latitude])
        
        params.extend([reference_date_taken, time_window_seconds])
        
        if reference_latitude and reference_longitude and prefer_same_location:
            params.extend([
                reference_latitude,
                reference_longitude,
                reference_latitude,
                location_tolerance_meters
            ])
        
        # Execute query
        _LOGGER.debug("Burst query params: %s", params)
        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        
        _LOGGER.debug("Burst query returned %d rows", len(rows))
        return [dict(row) for row in rows]
    
    async def get_file_by_id(self, file_id: int) -> dict | None:
        """Get file metadata by database ID.
        
        Args:
            file_id: Database ID of the file
            
        Returns:
            File record with metadata, or None if not found
        """
        async with self._db.execute(
            "SELECT * FROM media_files WHERE id = ?",
            (file_id,)
        ) as cursor:
            row = await cursor.fetchone()
        
        return dict(row) if row else None
    
    async def get_exif_by_file_id(self, file_id: int) -> dict | None:
        """Get EXIF data for a file by ID.
        
        Args:
            file_id: Database ID of the file
            
        Returns:
            EXIF data dictionary, or None if not found
        """
        async with self._db.execute(
            "SELECT * FROM exif_data WHERE file_id = ?",
            (file_id,)
        ) as cursor:
            row = await cursor.fetchone()
        
        return dict(row) if row else None
    
    async def update_favorite(self, file_path: str, is_favorite: bool) -> bool:
        """Update favorite status for a file.
        
        Args:
            file_path: Full path to the file
            is_favorite: True to mark as favorite, False to unmark
            
        Returns:
            True if successful, False if file not found
        """
        favorite_value = 1 if is_favorite else 0
        rating_value = 5 if is_favorite else 0
        
        # Update media_files table - set both is_favorited and rating
        async with self._db.execute(
            "UPDATE media_files SET is_favorited = ?, rating = ? WHERE path = ?",
            (favorite_value, rating_value, file_path)
        ) as cursor:
            rows_affected = cursor.rowcount
        
        # CRITICAL: Also update exif_data table - this is what get_random_files queries!
        # exif_data uses file_id (FK to media_files.id), so we need a subquery
        async with self._db.execute(
            """UPDATE exif_data 
               SET is_favorited = ?, rating = ?
               WHERE file_id = (SELECT id FROM media_files WHERE path = ?)""",
            (favorite_value, rating_value, file_path)
        ) as cursor:
            pass  # Context manager ensures proper cleanup
        
        await self._db.commit()
        
        if rows_affected > 0:
            return True
        else:
            _LOGGER.warning("File not found in database: %s", file_path)
            return False
    
    async def update_burst_metadata(self, burst_paths: list, favorited_paths: list) -> int:
        """Update burst_favorites and burst_count metadata for all files in a burst group.
        
        Args:
            burst_paths: List of all file paths in the burst
            favorited_paths: List of file paths that were marked as favorites
            
        Returns:
            Number of files successfully updated
        """
        import json
        
        # Store favorited filenames (not full paths) for portability
        favorited_filenames = [Path(p).name for p in favorited_paths]
        favorites_json = json.dumps(favorited_filenames) if favorited_filenames else None
        burst_count = len(burst_paths)
        
        updated_count = 0
        
        for file_path in burst_paths:
            try:
                # Update exif_data table with burst_favorites JSON and burst_count
                async with self._db.execute(
                    """UPDATE exif_data 
                       SET burst_favorites = ?, burst_count = ?
                       WHERE file_id = (SELECT id FROM media_files WHERE path = ?)""",
                    (favorites_json, burst_count, file_path)
                ) as cursor:
                    if cursor.rowcount > 0:
                        updated_count += 1
                        
            except Exception as e:
                _LOGGER.warning("Failed to update burst metadata for %s: %s", file_path, e)
        
        await self._db.commit()
        
        _LOGGER.debug(
            "Updated burst metadata for %d/%d files (burst_count=%d, %d favorited)", 
            updated_count, 
            len(burst_paths),
            burst_count,
            len(favorited_filenames)
        )
        
        return updated_count
    
    async def delete_file(self, file_path: str) -> bool:
        """Delete file record from database.
        
        Args:
            file_path: Full path to the file
            
        Returns:
            True if successful, False if file not found
        """
        # First get file_id for deleting related records
        async with self._db.execute(
            "SELECT id FROM media_files WHERE path = ?",
            (file_path,)
        ) as cursor:
            row = await cursor.fetchone()
        
        if not row:
            _LOGGER.warning("File not found in database: %s", file_path)
            return False
        
        file_id = row[0]
        
        # Delete EXIF data first (foreign key)
        await self._db.execute(
            "DELETE FROM exif_data WHERE file_id = ?",
            (file_id,)
        )
        
        # Delete file record
        await self._db.execute(
            "DELETE FROM media_files WHERE id = ?",
            (file_id,)
        )
        
        await self._db.commit()
        return True
    
    async def record_file_move(
        self, 
        original_path: str, 
        new_path: str, 
        reason: str = None
    ) -> None:
        """Record a file move to move_history table.
        
        Args:
            original_path: Original file path
            new_path: New file path
            reason: Reason for move (e.g., "edit", "junk")
        """
        import time
        
        await self._db.execute(
            """INSERT INTO move_history 
               (original_path, new_path, moved_at, move_reason, restored)
               VALUES (?, ?, ?, ?, 0)""",
            (original_path, new_path, int(time.time()), reason)
        )
        await self._db.commit()
        _LOGGER.debug("Recorded move: %s -> %s (reason: %s)", original_path, new_path, reason)
    
    async def get_pending_restores(self, folder_path: str = None) -> list:
        """Get list of files that can be restored.
        
        Args:
            folder_path: Optional filter by destination folder (e.g., "_Edit")
            
        Returns:
            List of move history records that haven't been restored
        """
        if folder_path:
            query = """SELECT id, original_path, new_path, moved_at, move_reason
                      FROM move_history 
                      WHERE restored = 0 AND new_path LIKE ?
                      ORDER BY moved_at DESC"""
            params = (f"%{folder_path}%",)
        else:
            query = """SELECT id, original_path, new_path, moved_at, move_reason
                      FROM move_history 
                      WHERE restored = 0
                      ORDER BY moved_at DESC"""
            params = ()
        
        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        
        return [
            {
                "id": row[0],
                "original_path": row[1],
                "new_path": row[2],
                "moved_at": row[3],
                "move_reason": row[4]
            }
            for row in rows
        ]
    
    async def mark_move_restored(self, move_id: int) -> None:
        """Mark a move as restored.
        
        Args:
            move_id: ID of the move_history record
        """
        import time
        
        await self._db.execute(
            """UPDATE move_history 
               SET restored = 1, restored_at = ?
               WHERE id = ?""",
            (int(time.time()), move_id)
        )
        await self._db.commit()
        _LOGGER.debug("Marked move %d as restored", move_id)
    
    async def cleanup_orphaned_exif(self) -> int:
        """Remove orphaned EXIF data rows that don't have corresponding media_files entries.
        
        Returns:
            Number of orphaned rows removed
        """
        # Count orphaned rows
        async with self._db.execute(
            "SELECT COUNT(*) FROM exif_data WHERE file_id NOT IN (SELECT id FROM media_files)"
        ) as cursor:
            row = await cursor.fetchone()
            orphaned_count = row[0] if row else 0
        
        if orphaned_count > 0:
            # Delete orphaned rows
            await self._db.execute(
                "DELETE FROM exif_data WHERE file_id NOT IN (SELECT id FROM media_files)"
            )
            await self._db.commit()
            _LOGGER.info("Removed %d orphaned exif_data rows", orphaned_count)
        
        return orphaned_count
    
    async def vacuum_database(self) -> None:
        """Run VACUUM to reclaim space and compact the database."""
        await self._db.execute("VACUUM")
        await self._db.commit()
        _LOGGER.debug("Database VACUUM completed")
    
    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            _LOGGER.info("Cache database connection closed")

