"""Admission wrapper for the collected arm of the one public result writer."""

from contextlib import closing

from .review_answer_handoff import registration_admission
from .review_collected_results import add_collected_review_results
from .review_answer_transport import SettledRegistrationRecovery, answer_input_error, input_error
from .storage import connect_initialized


def register(context, target, submission, *, finalization_basis=None, registration_recovery=None):
    """All physical checks precede the existing atomic core transaction.

    Collection intent remains unresolved after an uncertain response. Only the
    saved success or exact public reconciliation can acknowledge registration.
    """
    from .review_answer import AnswerError
    from .review_handoff_scope import ReviewHandoffScopeError
    from .review_handoff_transport import HandoffError
    from .review_wait_runtime.review_wait_repository import RepositoryError

    warnings = []
    core_committed = False
    try:
        if registration_recovery is not None:
            if type(registration_recovery) is not SettledRegistrationRecovery:
                raise input_error("registration_unknown", reason="effect_unknown")
            registration_recovery.require_submission(submission, finalization_basis)
        with registration_admission(context.repo, submission, context.caller) as admitted:
            if registration_recovery is None:
                admitted.dispatch()
            else:
                # This fresh admission, not an earlier audit or caller's request,
                # excludes every cooperating writer through commit/close/confirm.
                admitted.require_settled_recovery(registration_recovery, finalization_basis)
            with closing(connect_initialized(target)) as connection:
                with connection:
                    data = add_collected_review_results(connection, target.project,
                        context.args.task_id, submission, caller=context.caller,
                        database_target=target, finalization_basis=finalization_basis,
                        registration_recovery=registration_recovery,
                        user_approved_reviewers=context.args.user_approved_reviewers)
                # Only a successful transaction exit establishes the commit.
                # Connection close and admission exit can still fail later.
                core_committed = True
            admitted.confirm(data)
    except Exception as error:
        if core_committed:
            # Acknowledgement or cleanup cannot undo the known core result.
            warnings.append({"code": "collection_registration_acknowledgement_unavailable",
                "message": "Review results were recorded. Collection acknowledgement is unresolved; do not resubmit."})
        elif isinstance(error, AnswerError):
            raise answer_input_error(error) from None
        elif isinstance(error, (ReviewHandoffScopeError, HandoffError, RepositoryError)):
            raise input_error("collection_unavailable", reason="incomplete") from None
        else:
            raise
    return data, warnings
