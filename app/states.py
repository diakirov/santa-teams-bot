"""FSM-стани майстрів."""

from aiogram.fsm.state import State, StatesGroup


class CreateTeam(StatesGroup):
    name = State()
    kind = State()          # постійна / одноразова (inline)


class EnterCode(StatesGroup):
    code = State()


class FormFill(StatesGroup):
    full_name = State()
    phone = State()
    address = State()
    allergies = State()
    wishes = State()
    confirm = State()       # підсумок, рішення inline-кнопками
    paused = State()        # анкета на паузі після сторонньої команди


class AddMember(StatesGroup):
    username = State()


class ReportReason(StatesGroup):
    reason = State()


class BanReason(StatesGroup):
    reason = State()


class FeedbackText(StatesGroup):
    text = State()


class AdminReply(StatesGroup):
    """Відповідь адміна автору звернення."""
    text = State()


class UserReply(StatesGroup):
    """Відповідь автора звернення адміну."""
    text = State()
