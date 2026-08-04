# routes/jobs.py
# Job status and management API routes

from fastapi import APIRouter, HTTPException

from ..services.jobs import get_job, get_active_jobs, get_recent_jobs

router = APIRouter(tags=["Jobs"])


@router.get("/api/jobs")
def list_jobs():
    """Get all active jobs and recent completed jobs."""
    active = get_active_jobs()
    recent = get_recent_jobs(limit=5)

    # Merge lists, avoiding duplicates
    job_ids = {j["id"] for j in active}
    for job in recent:
        if job["id"] not in job_ids:
            active.append(job)

    return {"success": True, "jobs": active}


@router.get("/api/jobs/active")
def list_active_jobs():
    """Get only active (pending/running) jobs."""
    jobs = get_active_jobs()
    return {"success": True, "jobs": jobs}


@router.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Get status of a specific job."""
    job = get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Calculate percentage
    percentage = 0
    if job["total"] and job["total"] > 0:
        percentage = int((job["progress"] / job["total"]) * 100)

    return {
        "success": True,
        "job": {
            **job,
            "percentage": percentage
        }
    }
