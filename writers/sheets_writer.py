"""
Google Sheets Writer — ShamrockLeads

Dual-write support: writes arrest records to Google Sheets for backward
compatibility while MongoDB is the primary data store.

Features:
- 41-column output matching canonical schema v3.1
- Auto-creates county tabs
- Dedup by County + Booking_Number
- Qualified arrest cross-posting
- Ingestion logging
"""

import os
import json
import base64
from typing import List, Optional, Dict, Any
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from core.models import ArrestRecord
from scoring.lead_scorer import score_and_update


class SheetsWriter:
    """
    Google Sheets writer for arrest records.

    Writes ArrestRecord instances to Google Sheets with full 41-column schema v3.1
    including lead scoring fields.
    """

    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]

    DEFAULT_QUALIFIED_MIN_SCORE = 70
    QUALIFIED_SHEET_NAME = 'Qualified_Arrests'

    def __init__(
        self,
        spreadsheet_id: str,
        credentials_path: Optional[str] = None,
        qualified_min_score: int = DEFAULT_QUALIFIED_MIN_SCORE
    ):
        """
        Initialize the Sheets writer.

        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            credentials_path: Path to service account JSON credentials
            qualified_min_score: Minimum score for qualified arrests (default: 70)
        """
        self.spreadsheet_id = spreadsheet_id
        self.qualified_min_score = qualified_min_score

        # Initialize credentials
        # 1. Try direct JSON content from environment (raw JSON or Base64)
        if credentials_path is None:
            env_var = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON') or os.getenv('GOOGLE_SA_KEY_JSON')

            if env_var:
                try:
                    content = env_var.strip()
                    if not content.startswith('{'):
                        decoded = base64.b64decode(content).decode('utf-8')
                        service_account_info = json.loads(decoded)
                    else:
                        service_account_info = json.loads(content)

                    self.credentials = Credentials.from_service_account_info(
                        service_account_info,
                        scopes=self.SCOPES
                    )
                except Exception as e:
                    print(f"⚠️ Warning: Failed to parse Google Service Account env var: {e}")

        # 2. Fallback to file path
        if not hasattr(self, 'credentials'):
            if credentials_path is None:
                credentials_path = os.getenv('GOOGLE_SERVICE_ACCOUNT_KEY_PATH') or \
                                   os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

            if not credentials_path:
                raise ValueError(
                    "Credentials must be provided via "
                    "GOOGLE_SERVICE_ACCOUNT_JSON (env) or "
                    "GOOGLE_APPLICATION_CREDENTIALS (file path)"
                )

            self.credentials = Credentials.from_service_account_file(
                credentials_path,
                scopes=self.SCOPES
            )

        self.client = gspread.authorize(self.credentials)
        self.spreadsheet = self.client.open_by_key(spreadsheet_id)

    def write_records(
        self,
        records: List[ArrestRecord],
        county: str,
        auto_score: bool = True,
        deduplicate: bool = True
    ) -> Dict[str, Any]:
        """
        Write arrest records to the appropriate county sheet.

        Args:
            records: List of ArrestRecord instances
            county: County name (e.g., "Lee", "Collier")
            auto_score: Auto-score records if not already scored
            deduplicate: Remove duplicates

        Returns:
            Dictionary with write statistics
        """
        if not records:
            return {
                'total_records': 0,
                'new_records': 0,
                'duplicates_skipped': 0,
                'qualified_records': 0,
                'sheet_name': county
            }

        # Auto-score records if needed
        if auto_score:
            records = [score_and_update(r) if r.Lead_Score == 0 else r for r in records]

        # Get or create the county sheet
        sheet = self._get_or_create_sheet(county)

        # Ensure header row exists
        self._ensure_header_row(sheet)

        # Get existing records for deduplication
        existing_keys = set()
        if deduplicate:
            existing_keys = self._get_existing_dedup_keys(sheet)

        # Filter out duplicates
        new_records = []
        duplicates_skipped = 0

        for record in records:
            if deduplicate:
                dedup_key = record.get_dedup_key()
                if dedup_key in existing_keys:
                    duplicates_skipped += 1
                    continue
            new_records.append(record)

        # Write new records
        if new_records:
            rows = [record.to_sheet_row() for record in new_records]
            sheet.insert_rows(rows, row=2, value_input_option='USER_ENTERED')

        # Count qualified records
        qualified_count = sum(1 for r in new_records if r.is_qualified(self.qualified_min_score))

        # Also write qualified records to Qualified_Arrests sheet
        if qualified_count > 0:
            qualified_records = [r for r in new_records if r.is_qualified(self.qualified_min_score)]
            self._write_qualified_records(qualified_records)

        return {
            'total_records': len(records),
            'new_records': len(new_records),
            'duplicates_skipped': duplicates_skipped,
            'qualified_records': qualified_count,
            'sheet_name': county
        }

    def _write_qualified_records(self, records: List[ArrestRecord]) -> None:
        """Write qualified records to the Qualified_Arrests sheet."""
        if not records:
            return

        sheet = self._get_or_create_sheet(self.QUALIFIED_SHEET_NAME)
        self._ensure_header_row(sheet)

        existing_keys = self._get_existing_dedup_keys(sheet)

        new_records = []
        for record in records:
            dedup_key = record.get_dedup_key()
            if dedup_key not in existing_keys:
                new_records.append(record)

        if new_records:
            rows = [record.to_sheet_row() for record in new_records]
            sheet.insert_rows(rows, row=2, value_input_option='USER_ENTERED')

    def _get_or_create_sheet(self, sheet_name: str) -> gspread.Worksheet:
        """Get an existing sheet or create it if it doesn't exist."""
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            return self.spreadsheet.add_worksheet(
                title=sheet_name,
                rows=1000,
                cols=41
            )

    def _ensure_header_row(self, sheet: gspread.Worksheet) -> None:
        """Ensure the sheet has the correct 41-column header row (v3.1)."""
        try:
            existing_headers = sheet.row_values(1)
            if existing_headers == ArrestRecord.get_header_row():
                return
        except:
            pass

        headers = ArrestRecord.get_header_row()
        sheet.update('A1:AO1', [headers], value_input_option='USER_ENTERED')

        sheet.format('A1:AO1', {
            'textFormat': {'bold': True},
            'backgroundColor': {'red': 0.0, 'green': 0.66, 'blue': 0.42}
        })
        sheet.freeze(rows=1)

    def _get_existing_dedup_keys(self, sheet: gspread.Worksheet) -> set:
        """Get all existing dedup keys (County:Booking_Number) from sheet."""
        try:
            all_values = sheet.get_all_values()

            if len(all_values) <= 1:
                return set()

            dedup_keys = set()
            for row in all_values[1:]:
                if len(row) > 2:
                    county = row[1] if len(row) > 1 else ""
                    booking_number = row[2] if len(row) > 2 else ""
                    if booking_number and county:
                        dedup_keys.add(f"{county}:{booking_number}")

            return dedup_keys

        except Exception as e:
            print(f"Warning: Could not get existing dedup keys: {e}")
            return set()

    def log_ingestion(
        self,
        county: str,
        stats: Dict[str, Any],
        error: Optional[str] = None
    ) -> None:
        """Log an ingestion run to the Logs sheet."""
        try:
            logs_sheet = self._get_or_create_sheet('Logs')

            try:
                existing_headers = logs_sheet.row_values(1)
                if not existing_headers or existing_headers[0] != 'Timestamp':
                    logs_sheet.update('A1:H1', [[
                        'Timestamp', 'County', 'Total_Records', 'New_Records',
                        'Duplicates_Skipped', 'Qualified_Records', 'Status', 'Error'
                    ]], value_input_option='USER_ENTERED')
                    logs_sheet.format('A1:H1', {
                        'textFormat': {'bold': True},
                        'backgroundColor': {'red': 0.4, 'green': 0.5, 'blue': 0.9}
                    })
            except:
                pass

            timestamp = datetime.utcnow().isoformat()
            status = 'ERROR' if error else 'SUCCESS'

            log_row = [
                timestamp, county,
                stats.get('total_records', 0),
                stats.get('new_records', 0),
                stats.get('duplicates_skipped', 0),
                stats.get('qualified_records', 0),
                status, error or ''
            ]

            logs_sheet.append_row(log_row, value_input_option='USER_ENTERED')

        except Exception as e:
            print(f"Warning: Could not log ingestion: {e}")

    def get_qualified_records(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get qualified arrest records from the Qualified_Arrests sheet."""
        try:
            sheet = self.spreadsheet.worksheet(self.QUALIFIED_SHEET_NAME)
            records = sheet.get_all_records()

            if limit:
                records = records[:limit]

            return records

        except gspread.WorksheetNotFound:
            return []

    def clear_sheet(self, sheet_name: str, keep_header: bool = True) -> None:
        """Clear all data from a sheet."""
        try:
            sheet = self.spreadsheet.worksheet(sheet_name)

            if keep_header:
                sheet.batch_clear(['A2:AO10000'])
            else:
                sheet.clear()

        except gspread.WorksheetNotFound:
            print(f"Warning: Sheet '{sheet_name}' not found")


# Convenience function
def write_to_sheets(
    records: List[ArrestRecord],
    county: str,
    spreadsheet_id: str,
    credentials_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Convenience function to write records to Google Sheets.

    Args:
        records: List of ArrestRecord instances
        county: County name
        spreadsheet_id: Google Sheets spreadsheet ID
        credentials_path: Path to service account credentials (optional)

    Returns:
        Statistics dictionary
    """
    writer = SheetsWriter(spreadsheet_id, credentials_path)
    return writer.write_records(records, county)
