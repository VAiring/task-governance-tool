"""Exact optional public registration lookup, without transport or host I/O."""

import re

from .review_continuation import identifier
from .task_values import validation_error


def selection(args):
    preparation = getattr(args, "collection_preparation", None)
    submission = getattr(args, "collection_submission", None)
    if preparation is None and submission is None:
        return None
    if (not getattr(args, "audit", False) or not identifier(preparation, "tg_review_preparation_")
        or type(submission) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", submission) is None):
        raise validation_error("invalid_argument", "collection lookup requires --audit and both exact collection selectors")
    return preparation, submission


def read(connection, project_id, task_id, selectors):
    if selectors is None:
        return None
    from .review_answer_repository import read_registration
    return read_registration(connection, project_id=project_id, task_id=task_id,
        preparation_id=selectors[0], submission_digest=selectors[1])
