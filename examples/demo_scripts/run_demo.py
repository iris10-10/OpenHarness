"""Run the offline job-hunt matching demo from the repository root."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from openharness.jobhunt.parsing import parse_jd_text, parse_resume_text
from openharness.jobhunt.scoring import CandidateProfile, score_match


def main() -> int:
    sample_dir = ROOT / "examples" / "sample_data"
    resume_path = sample_dir / "resume.md"
    jd_path = sample_dir / "jobs" / "python-backend.md"
    resume_text = resume_path.read_text(encoding="utf-8")
    jd_text = jd_path.read_text(encoding="utf-8")

    candidate = CandidateProfile.from_resume(parse_resume_text(resume_text))
    result = score_match(candidate, parse_jd_text(jd_text))
    payload = {
        "job": parse_jd_text(jd_text).title,
        "company": parse_jd_text(jd_text).company,
        "score": result.total,
        "recommendation": result.recommendation,
        "covered_skills": list(result.covered_skills),
        "missing_skills": list(result.missing_skills),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
