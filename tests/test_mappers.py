"""Tests for API -> dataclass mapping.

Fixtures mirror real API payloads captured from the portal.
"""

from __future__ import annotations

from app.collector.api import epoch_ms_to_datetime, html_to_text
from app.collector.mappers import map_job_profile, map_notification

JOB_ITEM = {
    "companyCode": "5e482859-7daa-4649-bdaf-a6039cff4e89",
    "jobProfileLocation": "Bengaluru",
    "positionType": "INTERNSHIP",
    "positionTypeLabel": "Internship",
    "jobProfileIdentifier": "91926f33-688d-4e22-b672-ef991f1adb47",
    "jobProfileTitle": "Software Engineering Intern (Summer Internship)",
    "placementName": "Campus Placement Engineering 2027",
    "companyName": "VISA",
    "placementCategoryName": "Tier 2",
    "jobProfileApplicationDeadline": 1758947400000,
    "jobProfileStatus": "CLOSED_FOR_APPLICATIONS",
    "jobApplicationStatus": "APPLIED",
    "createdAt": 1758707809000,
    "documents": [],
}

JOB_DETAIL = {
    "offeredCtc": 900000,
    "jobProfileApplicationDeadline": 1758947400000,
    "jobProfile": {
        "identifier": "91926f33-688d-4e22-b672-ef991f1adb47",
        "title": "Software Engineering Intern (Summer Internship)",
        "location": "Bengaluru",
        "status": "CLOSED_FOR_APPLICATIONS",
        "positionType": "INTERNSHIP",
        "jobDescription": "<p>Greetings from VISA!</p><p>&nbsp;</p><p>Build things.</p>",
        "ctcMin": 900000,
        "ctcMax": 1200000,
        "ctcInterval": "MONTH",
        "ctcCurrency": "INR",
        "effectiveCTC": 90000,
        "resumeMandatory": True,
        "noOfVacancies": 5,
        "applicableCourses": [{"courseName": "B.Tech CSE"}, {"courseName": "B.E ECE"}],
        "eligibilities": [{"name": "CGPA >= 7.0"}],
    },
    "eligibilityCheckResult": {"result": True},
}

NOTIFICATION = {
    "uuid": "b5d5772c-0790-48e9-96a9-79877b7735a8",
    "content": "Job profile <strong>Application Developer - Associate</strong> "
               "is open for applications (opened by Training and Placement BMSCE)",
    "action": "UPDATE_STATUS",
    "object": "JOBPROFILE",
    "urlParamMap": {
        "account_code": "bms_co_en_eb_16",
        "company_code": "d9c05f53",
        "job_profile_id": "50f7cefa-8b72-4bb4-927c-d81a7cd83193",
        "placement_id": "9b49737f",
    },
    "status": "STORED",
    "createdAt": 1790398515000,
}


def test_epoch_ms_conversion():
    dt = epoch_ms_to_datetime(1758947400000)
    assert dt is not None and dt.year == 2025
    assert epoch_ms_to_datetime(None) is None
    assert epoch_ms_to_datetime(0) is None


def test_html_to_text_strips_markup():
    assert html_to_text("<p>Hello</p><p>&nbsp;</p><p>World</p>") == "Hello\nWorld"
    assert html_to_text(None) == ""


def test_map_job_profile_uses_stable_id_and_deadline():
    raw = map_job_profile(JOB_ITEM, JOB_DETAIL)
    assert raw.external_id == "91926f33-688d-4e22-b672-ef991f1adb47"
    assert raw.title == "Software Engineering Intern (Summer Internship)"
    assert raw.company_name == "VISA"
    assert raw.location == "Bengaluru"
    assert raw.status == "APPLIED"
    assert raw.raw["deadline_epoch_ms"] == 1758947400000
    assert raw.raw["source"] == "api"


def test_map_job_profile_detail_fields():
    raw = map_job_profile(JOB_ITEM, JOB_DETAIL)
    assert "Greetings from VISA!" in raw.description
    assert "Build things." in raw.description
    assert "CTC" in (raw.compensation or "")
    assert "-0" not in (raw.compensation or "")
    assert raw.eligibility == "Eligible"
    assert raw.deadline is not None and raw.deadline.year == 2025
    assert raw.raw["applicable_courses"] == ["B.Tech CSE", "B.E ECE"]


def test_map_job_profile_without_detail_is_still_usable():
    raw = map_job_profile(JOB_ITEM, None)
    assert raw.external_id == "91926f33-688d-4e22-b672-ef991f1adb47"
    assert raw.description == ""
    assert raw.compensation is None  # unknown stays unknown, never guessed


def test_map_notification_builds_job_url():
    ann = map_notification(NOTIFICATION, "https://app.joinsuperset.com")
    assert ann.external_id == "b5d5772c-0790-48e9-96a9-79877b7735a8"
    assert ann.category == "JOBPROFILE"
    assert ann.url == (
        "https://app.joinsuperset.com/students/jobprofiles"
        "?currentJobId=50f7cefa-8b72-4bb4-927c-d81a7cd83193"
    )
    assert "<strong>" not in ann.title
    assert ann.published_at is not None


def test_map_notice_notification_url():
    item = dict(NOTIFICATION, object="STUDENT_NOTICE",
                urlParamMap={"notice_uuid": "cb68495e-8424-4f26-a132-ec378f70d26d"})
    ann = map_notification(item, "https://app.joinsuperset.com")
    assert ann.url.endswith("/students/notices/cb68495e-8424-4f26-a132-ec378f70d26d")


def test_map_job_profile_raw_is_json_serialisable():
    import json

    raw = map_job_profile(JOB_ITEM, JOB_DETAIL)
    # This dict is persisted to a JSON column; datetimes would break the write.
    json.dumps(raw.raw)
    assert isinstance(raw.raw["created_at"], str)


def test_single_line_notification_keeps_full_body():
    ann = map_notification(NOTIFICATION, "https://app.joinsuperset.com")
    assert "is open for applications" in ann.body
    assert "<strong>" not in ann.body


def test_ineligible_boolean_is_rendered():
    detail = {**JOB_DETAIL, "eligibilityCheckResult": {"result": False}}
    assert map_job_profile(JOB_ITEM, detail).eligibility == "Not eligible"


def test_missing_eligibility_falls_back_to_criteria():
    """No verdict from the server -> fall back to the published criteria."""
    detail = {**JOB_DETAIL, "eligibilityCheckResult": {}}
    assert map_job_profile(JOB_ITEM, detail).eligibility == "CGPA >= 7.0"


def test_unknown_when_nothing_is_published():
    detail = {**JOB_DETAIL, "eligibilityCheckResult": {}}
    detail["jobProfile"] = {
        **detail["jobProfile"],
        "eligibilities": [],
        "genericEligibilities": [],
        "workExEligibilities": [],
    }
    assert map_job_profile(JOB_ITEM, detail).eligibility is None
