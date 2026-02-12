# Database Integration Proposal

## Current State
- **In-memory storage**: All data (detections, AIS, weather, webcams, APRS) stored in Python dictionaries
- **File-based persistence**: CSV files for detections, JSON for configs
- **No persistence**: AIS vessels, weather, webcams, APRS stations lost on restart
- **Slow page loads**: Must wait for API calls to populate data

## Proposed Solution: SQLite Database

### Benefits
1. **Fast page loads**: Query recent data instantly on page load
2. **Persistence**: All data survives restarts
3. **Historical data**: Query past detections, weather, vessels
4. **Better queries**: Filter, search, aggregate data efficiently
5. **Lightweight**: No server needed, single file database
6. **Built-in**: Python includes sqlite3, no extra dependencies

### Database Schema
See `database_schema.sql` for full schema including:
- `detections` - Drone detection history
- `ais_vessels` - Maritime vessel data
- `weather_data` - Weather forecast data
- `webcams` - Webcam locations and data
- `aprs_stations` - APRS radio stations
- `faa_cache` - FAA lookup cache
- `aliases` - Device aliases
- `zones` - Airspace restrictions
- `incidents` - Zone violations and events

### Implementation Strategy

#### Phase 1: Database Setup
1. Add SQLite database initialization
2. Create schema on first run
3. Add database connection management

#### Phase 2: Data Migration
1. Migrate existing CSV/JSON data to database
2. Update write operations to use database
3. Keep CSV exports for compatibility

#### Phase 3: Fast Page Loads
1. Add API endpoints to query recent data from database
2. Update frontend to load from database on page load
3. Keep WebSocket for real-time updates

#### Phase 4: Historical Queries
1. Add time-range queries
2. Add filtering and search
3. Add statistics endpoints

### Example API Endpoints

```python
# Get recent detections (last 5 minutes)
GET /api/detections/recent

# Get recent AIS vessels (last 10 minutes)  
GET /api/ais_vessels/recent

# Get recent weather (last 10 minutes)
GET /api/weather/recent

# Get historical detections
GET /api/detections/history?start=timestamp&end=timestamp&mac=xxx

# Get statistics
GET /api/stats/detections?hours=24
```

### Migration Path
1. **Backward compatible**: Keep existing CSV/JSON files
2. **Gradual migration**: Write to both database and files initially
3. **Optional**: Make database primary, files as backup/export

### Performance Considerations
- **Indexes**: All frequently queried fields indexed
- **Views**: Pre-filtered views for common queries
- **Cleanup**: Periodic cleanup of old data (configurable retention)
- **Connection pooling**: Reuse database connections

### Data Retention
- **Recent data**: Keep in memory for fast access
- **Database**: Store all data with configurable retention
- **Archival**: Option to export old data to CSV/KML

## Next Steps
1. Review and approve schema
2. Implement database layer
3. Add migration scripts
4. Update API endpoints
5. Update frontend to use new endpoints

