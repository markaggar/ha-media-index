"""SQLite cache manager for media file indexing."""
import aiosqlite
import logging
import os
from datetime import datetime
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
            
            # Create schema
            await self._create_schema()
            
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
            'white_balance': 'TEXT'
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
        
        # Get files with location data
        async with self._db.execute(
            "SELECT COUNT(*) FROM exif_data WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
        ) as cursor:
            row = await cursor.fetchone()
            files_with_location = row[0] if row else 0
        
        # Get geocode cache stats
        async with self._db.execute("SELECT COUNT(*) FROM geocode_cache") as cursor:
            row = await cursor.fetchone()
            geocode_cache_entries = row[0] if row else 0
        
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
        
        await self._db.execute("""
            INSERT OR REPLACE INTO media_files 
            (path, filename, folder, file_type, file_size, modified_time, 
             created_time, last_scanned, width, height, orientation,
             is_favorited, rating, rated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 
                    COALESCE((SELECT is_favorited FROM media_files WHERE path = ?), ?),
                    COALESCE((SELECT rating FROM media_files WHERE path = ?), ?),
                    (SELECT rated_at FROM media_files WHERE path = ?))
        """, (
            file_data['path'],
            file_data['filename'],
            file_data['folder'],
            file_data['file_type'],
            file_data.get('file_size'),
            file_data['modified_time'],
            file_data.get('created_time'),
            last_scanned_value,  # Use computed value instead of always current_time
            file_data.get('width'),
            file_data.get('height'),
            file_data.get('orientation'),
            # Preserve existing is_favorited/rating/rated_at if row exists, else use defaults
            file_data['path'], file_data.get('is_favorited', 0),
            file_data['path'], file_data.get('rating', 0),
            file_data['path'],
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
        async with self._db.execute("""
            SELECT location_name, location_city, location_state, location_country
            FROM geocode_cache
            WHERE latitude = ? AND longitude = ? AND precision_level = ?
        """, (round(latitude, 3), round(longitude, 3), 3)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    'location_name': row[0],
                    'location_city': row[1],
                    'location_state': row[2],
                    'location_country': row[3]
                }
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
            
            # Date filtering: null means "no limit" in that direction
            # Use EXIF date_taken if available, fallback to created_time
            if date_from is not None:
                # Validate date_from is a valid date string using datetime.strptime
                try:
                    from datetime import datetime
                    date_from_str = str(date_from) if not isinstance(date_from, str) else date_from
                    # Proper validation with datetime.strptime - prevents invalid dates like 2024-13-45
                    datetime.strptime(date_from_str, "%Y-%m-%d")
                    new_files_query += " AND DATE(COALESCE(e.date_taken, m.created_time), 'unixepoch') >= ?"
                    params.append(date_from_str)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning("Invalid date_from parameter: %s - %s", date_from, e)
            
            if date_to is not None:
                # Validate date_to is a valid date string using datetime.strptime
                try:
                    from datetime import datetime
                    date_to_str = str(date_to) if not isinstance(date_to, str) else date_to
                    # Proper validation with datetime.strptime - prevents invalid dates like 2024-13-45
                    datetime.strptime(date_to_str, "%Y-%m-%d")
                    new_files_query += " AND DATE(COALESCE(e.date_taken, m.created_time), 'unixepoch') <= ?"
                    params.append(date_to_str)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning("Invalid date_to parameter: %s - %s", date_to, e)
            
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
            
            # Date filtering: null means "no limit" in that direction
            # Use EXIF date_taken if available, fallback to created_time
            if date_from is not None:
                # Validate date_from is a valid date string
                try:
                    date_from_str = str(date_from) if not isinstance(date_from, str) else date_from
                    # Quick validation that it looks like a date (YYYY-MM-DD format)
                    if len(date_from_str) != 10 or date_from_str.count('-') != 2:
                        raise ValueError(f"Invalid date_from format: {date_from_str}")
                    query += " AND DATE(COALESCE(e.date_taken, m.created_time), 'unixepoch') >= ?"
                    params.append(date_from_str)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning("Invalid date_from parameter: %s - %s", date_from, e)
            
            if date_to is not None:
                # Validate date_to is a valid date string
                try:
                    date_to_str = str(date_to) if not isinstance(date_to, str) else date_to
                    # Quick validation that it looks like a date (YYYY-MM-DD format)
                    if len(date_to_str) != 10 or date_to_str.count('-') != 2:
                        raise ValueError(f"Invalid date_to format: {date_to_str}")
                    query += " AND DATE(COALESCE(e.date_taken, m.created_time), 'unixepoch') <= ?"
                    params.append(date_to_str)
                except (ValueError, TypeError) as e:
                    _LOGGER.warning("Invalid date_to parameter: %s - %s", date_to, e)
            
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
        
        # Date filtering: null means "no limit" in that direction
        # Use EXIF date_taken if available, fallback to created_time
        if date_from is not None:
            # Validate date_from is a valid date string
            try:
                from datetime import datetime
                date_from_str = str(date_from) if not isinstance(date_from, str) else date_from
                datetime.strptime(date_from_str, "%Y-%m-%d")
                query += " AND DATE(COALESCE(e.date_taken, m.created_time), 'unixepoch') >= ?"
                params.append(date_from_str)
            except (ValueError, TypeError) as e:
                _LOGGER.warning("Invalid date_from parameter: %s - %s", date_from, e)
        
        if date_to is not None:
            # Validate date_to is a valid date string
            try:
                from datetime import datetime
                date_to_str = str(date_to) if not isinstance(date_to, str) else date_to
                datetime.strptime(date_to_str, "%Y-%m-%d")
                query += " AND DATE(COALESCE(e.date_taken, m.created_time), 'unixepoch') <= ?"
                params.append(date_to_str)
            except (ValueError, TypeError) as e:
                _LOGGER.warning("Invalid date_to parameter: %s - %s", date_to, e)
            
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
        order_direction: str = "desc"
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
        direction = allowed_directions.get(order_direction.lower(), "ASC")
        query += f" ORDER BY {sort_field} {direction} LIMIT ?"
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
    
    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            _LOGGER.info("Cache database connection closed")

