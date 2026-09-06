from dataclasses import dataclass, field
from enum import Enum
from typing import List
from src.utils.config import Config

class Severity(str, Enum):
    CRITICAL = 'critical'
    MAJOR = 'major'
    MINOR = 'minor'

@dataclass
class Issue:
    severity: Severity
    location: str
    description: str
    suggestion: str
    confidence_score: float

    @property
    def is_convincing(self) -> bool:
        if self.severity == Severity.CRITICAL:
            return self.confidence_score >= Config.CRITICAL_CONFIDENCE_THRESHOLD
        if self.severity == Severity.MAJOR:
            return self.confidence_score >= Config.MAJOR_CONFIDENCE_THRESHOLD
        if self.severity == Severity.MINOR:
            return self.confidence_score >= Config.MINOR_CONFIDENCE_THRESHOLD
        return False

@dataclass
class CriterionResult:
    criterion: str
    issues: List[Issue] = field(default_factory=list)

    @property
    def convincing_critical_issues(self) -> List[Issue]:
        return [i for i in self.issues if i.is_convincing]

    @property
    def passed(self) -> bool:
        return len(self.convincing_critical_issues) == 0

@dataclass
class SlideReview:
    slide_index: int
    slide_title: str
    criteria: dict[str, CriterionResult]
    protected: List[str] = field(default_factory=list)

    @property
    def convincing_critical_issues(self) -> List[Issue]:
        issues = []
        for cr in self.criteria.values():
            issues.extend(cr.convincing_critical_issues)
        return issues

    @property
    def convincing_issues(self) -> List[Issue]:
        issues = []
        for cr in self.criteria.values():
            issues.extend([i for i in cr.issues if i.is_convincing])
        return issues

    @property
    def minor_issues(self) -> List[Issue]:
        issues = []
        for cr in self.criteria.values():
            issues.extend([i for i in cr.issues if i.severity == Severity.MINOR])
        return issues

    @property
    def passed(self) -> bool:
        return len(self.convincing_critical_issues) == 0

@dataclass
class WriterReview:
    slide_reviews: List[SlideReview]

    @property
    def failed_slides(self) -> List[SlideReview]:
        return [s for s in self.slide_reviews if not s.passed]

    @property
    def convincing_critical_count(self) -> int:
        return sum((len(s.convincing_critical_issues) for s in self.slide_reviews))

    @property
    def convincing_major_count(self) -> int:
        return sum((len([i for i in s.convincing_issues if i.severity == Severity.MAJOR]) for s in self.slide_reviews))

    @property
    def convincing_minor_count(self) -> int:
        return sum((len([i for i in s.convincing_issues if i.severity == Severity.MINOR]) for s in self.slide_reviews))

    @property
    def minor_count(self) -> int:
        return sum((len(s.minor_issues) for s in self.slide_reviews))

    @property
    def passed(self) -> bool:
        return len(self.failed_slides) == 0

@dataclass
class Version:
    iteration: int
    slides: dict[int, str]
    review: WriterReview

    def is_better_than(self, other: 'Version') -> bool:
        if self.review.convincing_critical_count != other.review.convincing_critical_count:
            return self.review.convincing_critical_count < other.review.convincing_critical_count
        return self.review.minor_count < other.review.minor_count

    def get_slides_to_fix(self) -> List[SlideReview]:
        return self.review.failed_slides

@dataclass
class PlannerReview:
    criteria: dict[str, CriterionResult]

    @property
    def convincing_critical_issues(self) -> List[Issue]:
        issues = []
        for cr in self.criteria.values():
            issues.extend(cr.convincing_critical_issues)
        return issues

    @property
    def passed(self) -> bool:
        return len(self.convincing_critical_issues) == 0

    @property
    def decision(self) -> str:
        if self.passed:
            return 'ACCEPT'
        return 'RETRY'