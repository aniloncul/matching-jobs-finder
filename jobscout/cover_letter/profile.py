from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ProfileError(Exception):
    """Raised when the applicant profile is missing or incomplete."""


# These fields are rendered by the template as fixed sentences in the conclusion.
# They are kept out of the LLM prompt so the model never repeats them.
TEMPLATE_RENDERED_FIELDS = (
    "earliest_start",
    "notice_period_months",
    "confidential_application",
)


class Experience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company: str = ""
    role: str = ""
    start: str = ""
    end: str = ""
    location: str = ""
    achievements: list[str] = Field(default_factory=list)


class Education(BaseModel):
    model_config = ConfigDict(extra="ignore")

    institution: str = ""
    degree: str = ""
    start: str = ""
    end: str = ""
    location: str = ""


class Profile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    headline: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    summary: str = ""
    motivation: str = ""
    skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    earliest_start: str = ""
    notice_period_months: int = 0
    confidential_application: bool = False
    cv_text: str = ""

    def prompt_text(self) -> str:
        data = self.model_dump(mode="json")
        for key in TEMPLATE_RENDERED_FIELDS:
            data.pop(key, None)
        compact = {
            key: value
            for key, value in data.items()
            if value != "" and value != [] and value is not None
        }
        return yaml.safe_dump(compact, sort_keys=False, allow_unicode=True)

    def readiness_issues(self) -> list[str]:
        issues: list[str] = []
        name = self.name.strip()
        email = self.email.strip()
        if not name or name.lower() == "your name":
            issues.append("Set your real name.")
        if not email or email.lower() in {"you@example.com", "name@example.com"}:
            issues.append("Set your real email address.")
        has_cv = len(self.cv_text.strip()) >= 120
        if not self.summary.strip() and not has_cv:
            issues.append("Add a professional summary or paste your CV text.")
        if not has_cv:
            if not self.skills:
                issues.append("Add at least one skill or paste your CV text.")
            if not self.experience:
                issues.append("Add at least one work experience entry or paste your CV text.")
            elif not any(
                item.company.strip()
                and item.role.strip()
                and any(achievement.strip() for achievement in item.achievements)
                for item in self.experience
            ):
                issues.append("Add a company, role and at least one achievement to your experience.")
        if any("example" in item.company.lower() for item in self.experience):
            issues.append("Replace the example company with a real employer.")
        if any(
            "example" in item.institution.lower() or "example" in item.degree.lower()
            for item in self.education
        ):
            issues.append("Replace or remove the example education entry.")
        if any("example" in item.lower() for item in self.certifications):
            issues.append("Replace or remove the example certification.")
        if any("example" in item.lower() for item in self.soft_skills):
            issues.append("Replace or remove the example soft skill.")
        if "optional: paste" in self.cv_text.lower() or "example" in self.cv_text.lower():
            issues.append("Replace or clear the example CV text.")
        links = " ".join((self.linkedin, self.github, self.website)).lower()
        if "your-profile" in links or "example.com" in links:
            issues.append("Replace the example profile links.")
        if "street 1" in self.address.lower() or "000 000" in self.phone:
            issues.append("Replace the example address or phone number.")
        if self.postal_code.strip() in {"12345", "00000", "000 000"}:
            issues.append("Replace the example postal code.")
        if "short professional summary" in self.summary.lower():
            issues.append("Replace the example professional summary.")
        if self.notice_period_months < 0:
            issues.append("The notice period cannot be negative.")
        if self.notice_period_months > 12:
            issues.append("The notice period should be 12 months or less.")
        return issues

    def readiness_issue(self) -> str | None:
        issues = self.readiness_issues()
        return issues[0] if issues else None


class _ProfileDumper(yaml.SafeDumper):
    pass


def _represent_str(dumper: yaml.SafeDumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_ProfileDumper.add_representer(str, _represent_str)


def save_profile(path: str | Path, profile: Profile) -> None:
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    data = profile.model_dump(mode="python")
    data["skills"] = [item for item in data.get("skills", []) if str(item).strip()]
    data["soft_skills"] = [
        item for item in data.get("soft_skills", []) if str(item).strip()
    ]
    data["languages"] = [item for item in data.get("languages", []) if str(item).strip()]
    data["certifications"] = [
        item for item in data.get("certifications", []) if str(item).strip()
    ]
    for entry in data.get("experience", []):
        entry["achievements"] = [
            item for item in entry.get("achievements", []) if str(item).strip()
        ]
    text = yaml.dump(
        data,
        Dumper=_ProfileDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )
    temporary = profile_path.with_name(f".{profile_path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    if profile_path.exists():
        backup = profile_path.with_suffix(profile_path.suffix + ".bak")
        backup.write_text(profile_path.read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(temporary, profile_path)


def inspect_profile(path: str | Path) -> tuple[Profile | None, list[str]]:
    profile_path = Path(path)
    if not profile_path.exists():
        return None, [
            f"Profile not found at {profile_path}. Copy config/profile.example.yaml to config/profile.yaml and fill it in."
        ]
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return None, [f"Cannot read profile: {exc}"]
    if not isinstance(raw, dict):
        return None, ["config/profile.yaml must contain a YAML mapping"]
    try:
        profile = Profile.model_validate(raw)
    except Exception as exc:
        return None, [f"Invalid profile: {exc}"]
    return profile, profile.readiness_issues()


def load_profile(path: str | Path) -> Profile:
    profile, issues = inspect_profile(path)
    if profile is None or issues:
        detail = " ".join(issues) or "Profile is incomplete."
        raise ProfileError(detail)
    return profile
