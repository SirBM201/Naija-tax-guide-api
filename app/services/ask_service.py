# app/services/ask_service.py - COMPLETE VERSION
# (Copy from your existing file but make sure it has the ask_guarded function at the end)

# ... [all your existing code up to the finalize_ai_success function] ...

# Then ensure these functions exist at the end of the file:

def process_ask_request(
    question: str,
    *,
    account_id: Optional[str] = None,
    lang: str = "en",
    channel: str = "web",
    account: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _process_ask_request(
        question=question,
        account_id=account_id,
        lang=lang,
        channel=channel,
        account=account,
    )


def handle_ask_request(
    question: str,
    *,
    account_id: Optional[str] = None,
    lang: str = "en",
    channel: str = "web",
    account: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _process_ask_request(
        question=question,
        account_id=account_id,
        lang=lang,
        channel=channel,
        account=account,
    )


def ask_question(
    question: str,
    *,
    account_id: Optional[str] = None,
    lang: str = "en",
    channel: str = "web",
    account: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _process_ask_request(
        question=question,
        account_id=account_id,
        lang=lang,
        channel=channel,
        account=account,
    )


def execute_ask(
    question: str,
    *,
    account_id: Optional[str] = None,
    lang: str = "en",
    channel: str = "web",
    account: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _process_ask_request(
        question=question,
        account_id=account_id,
        lang=lang,
        channel=channel,
        account=account,
    )


def ask_guarded(
    question: str,
    *,
    account_id: Optional[str] = None,
    lang: str = "en",
    channel: str = "web",
    account: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _process_ask_request(
        question=question,
        account_id=account_id,
        lang=lang,
        channel=channel,
        account=account,
    )

