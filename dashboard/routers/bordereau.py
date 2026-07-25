"""
Bordereau API Blueprint — ShamrockLeads
Endpoints for generating official OSI and Palmetto Monthly Surety Bordereau Reports.
"""

from fastapi import APIRouter, Request, Query
from starlette.responses import Response
from fastapi.responses import JSONResponse
from typing import Optional

from dashboard.services.surety_bordereau_service import generate_bordereau_data, export_bordereau_csv

bordereau_bp = APIRouter(prefix="/api/reports", tags=["reports"])

@bordereau_bp.get("/bordereau")
async def get_bordereau_report(
    surety: str = Query("osi", description="osi or palmetto"),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    fmt: str = Query("json", description="json or csv")
):
    """
    Generate the official monthly Surety Bordereau liability report for OSI or Palmetto.
    """
    try:
        data = await generate_bordereau_data(surety_id=surety, year=year, month=month)
        if fmt.lower() == "csv":
            csv_str = export_bordereau_csv(data)
            filename = f"Bordereau_{surety.upper()}_{data.get('reporting_period', 'monthly')}.csv"
            return Response(
                content=csv_str,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        return JSONResponse(status_code=200, content=data)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"Bordereau generation failed: {str(exc)}"})
