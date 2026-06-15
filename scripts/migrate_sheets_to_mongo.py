import os
import sys
import logging
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.append(str(PROJECT_ROOT))

from config.settings import settings
from writers.sheets_writer import SheetsWriter
from writers.mongo_writer import MongoWriter
from core.models import ArrestRecord

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("migrate_sheets_to_mongo")

def main():
    logger.info("Starting Google Sheets to MongoDB full migration...")

    # Initialize writers
    if not settings.sheets_configured() or not settings.mongo_configured():
        logger.error("Both Google Sheets and MongoDB must be configured in .env.")
        sys.exit(1)

    try:
        sheets_writer = SheetsWriter(
            spreadsheet_id=settings.GOOGLE_SPREADSHEET_ID,
            credentials_path=settings.GOOGLE_APPLICATION_CREDENTIALS
        )
        mongo_writer = MongoWriter()
    except Exception as e:
        logger.error(f"Failed to initialize writers: {e}")
        sys.exit(1)

    worksheets = sheets_writer.spreadsheet.worksheets()
    logger.info(f"Found {len(worksheets)} worksheets.")

    # Sheets to ignore
    ignore_sheets = {
        "Logs", 
        "Qualified_Arrests", 
        "IntakeQueue", 
        "PaymentLog", 
        "CheckInLog", 
        "Ingestion_Log",
        "Config"
    }

    total_records_processed = 0
    total_new = 0
    total_updated = 0

    for ws in worksheets:
        title = ws.title
        if title in ignore_sheets:
            logger.info(f"Skipping ignored sheet: {title}")
            continue

        logger.info(f"Processing sheet: {title}")
        
        try:
            records_data = ws.get_all_records()
        except Exception as e:
            logger.error(f"Failed to fetch records from {title}: {e}")
            continue
            
        if not records_data:
            logger.info(f"No records found in {title}.")
            continue

        logger.info(f"Found {len(records_data)} records in {title}. Converting to ArrestRecord...")
        
        arrest_records = []
        for row in records_data:
            # Map dict to ArrestRecord using the column headers
            # Note: get_all_records uses the first row as dictionary keys
            try:
                record = ArrestRecord.from_dict(row)
                arrest_records.append(record)
            except Exception as e:
                # Some rows might be malformed
                continue
                
        if not arrest_records:
            continue

        logger.info(f"Upserting {len(arrest_records)} records into MongoDB for county {title}...")
        
        chunk_size = 1000
        sheet_new = 0
        sheet_updated = 0
        
        for i in range(0, len(arrest_records), chunk_size):
            chunk = arrest_records[i:i + chunk_size]
            logger.info(f"  -> Upserting chunk {i//chunk_size + 1}/{(len(arrest_records)-1)//chunk_size + 1} ({len(chunk)} records)...")
            try:
                stats = mongo_writer.write_records(chunk, county=title)
                sheet_new += stats['new_records']
                sheet_updated += stats['updated_records']
            except Exception as e:
                logger.error(f"  -> Error upserting chunk: {e}")
                
        logger.info(f"[{title}] Stats: {sheet_new} new, {sheet_updated} updated (Dedup working).")
        
        total_records_processed += len(arrest_records)
        total_new += sheet_new
        total_updated += sheet_updated

    logger.info("="*50)
    logger.info("MIGRATION COMPLETE")
    logger.info(f"Total Sheets Processed: {len(worksheets) - len(ignore_sheets)}")
    logger.info(f"Total Records Processed: {total_records_processed}")
    logger.info(f"Total Newly Inserted: {total_new}")
    logger.info(f"Total Updated: {total_updated}")
    logger.info("="*50)

if __name__ == "__main__":
    main()
