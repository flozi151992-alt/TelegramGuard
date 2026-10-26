import logging
import re
import time
from collections import defaultdict, deque

from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import ApplicationHandlerStop, ContextTypes

log = logging.getLogger(__name__)

# Списки стартовые, дополни своими. Корни пиши в нижнем регистре.
PROFANITY = ["хуй", "нахуй", "похуй", "пизд", "блят", "бляд", "мудак", "сука",
             "ебал", "ебан", "ебат", "заеб", "отъеб", "съеб"]
ADULT = ["порн", "секс", "эротик", "стриптиз"]
ADULT_DOMAINS = ["pornhub", "xvideos", "xhamster"]
SPAM = ["в лс", "в личку", "пишите в лс", "казино", "заработок", "быстрый доход"]

MAX_LINKS = 2              # больше ссылок в одном сообщении — спам
NEW_LIMIT = 10             # первые N сообщений в чате — участник "новый"
BLOCK_LINKS_FROM_NEW = False

LEET = str.maketrans({"0": "о", "3": "е", "4": "а", "@": "а", "1": "и", "$": "с", "ё": "е"})
LINK_RE = re.compile(r"(?:https?://|www\.|t\.me/)\S+", re.I)
AGE_RE = re.compile(r"18\s*\+")


def normalize(text: str) -> str:
    t = text.lower().translate(LEET)
    t = re.sub(r"[^\w\s]", "", t)     # х.у.й, х*у*й -> хуй
    t = re.sub(r"(.)\1+", r"\1", t)   # хууй -> хуй
    return re.sub(r"\s+", " ", t).strip()


def _contains(norm: str, root: str) -> bool:
    # Короткие корни ищем только с начала слова, чтобы «еб» не срабатывало на «небо»
    if len(root) <= 3:
        return re.search(rf"(?<![а-я0-9a-z]){re.escape(root)}", norm) is not None
    return root in norm


P_PROF = [normalize(w) for w in PROFANITY]
P_ADULT = [normalize(w) for w in ADULT]
P_SPAM = [normalize(w) for w in SPAM]

seen: defaultdict[tuple[int, int], int] = defaultdict(int)
recent: defaultdict[tuple[int, int], deque] = defaultdict(deque)
strikes: defaultdict[tuple[int, int], int] = defaultdict(int)


def find_violation(raw: str, key: tuple[int, int]) -> str | None:
    low = raw.lower()
    norm = normalize(raw)
    now = time.time()
    seen[key] += 1
    links = len(LINK_RE.findall(raw))

    if AGE_RE.search(low) or any(d in low for d in ADULT_DOMAINS) or any(_contains(norm, w) for w in P_ADULT):
        return "18+ контент"
    if any(_contains(norm, w) for w in P_PROF):
        return "мат"
    if links > MAX_LINKS:
        return "слишком много ссылок"
    if links and BLOCK_LINKS_FROM_NEW and seen[key] <= NEW_LIMIT:
        return "ссылка от нового участника"
    if any(_contains(norm, w) for w in P_SPAM):
        return "спам"

    q = recent[key]
    q.append((now, norm))
    while q and now - q[0][0] > 10:
        q.popleft()
    if len(q) > 5 or sum(1 for _, t in q if t == norm) >= 3:
        return "флуд"
    return None


async def moderate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if msg is None or user is None or chat is None or user.is_bot:
        return
    text = msg.text or msg.caption or ""
    if not text:
        return

    member = await context.bot.get_chat_member(chat.id, user.id)
    if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return

    key = (chat.id, user.id)
    reason = find_violation(text, key)
    if reason is None:
        return

    try:
        await msg.delete()
    except Exception:
        log.exception("Не удалось удалить сообщение: проверь права бота в группе")
        return

    if reason == "18+ контент":
        strikes[key] = 3          # 18+ сразу баном
    else:
        strikes[key] += 1

    if strikes[key] >= 3:
        await context.bot.ban_chat_member(chat.id, user.id)
        await context.bot.send_message(chat.id, f"{user.full_name} заблокирован за нарушения.")
    else:
        await context.bot.send_message(
            chat.id,
            f"{user.full_name}, сообщение удалено: {reason}. Предупреждение {strikes[key]}/3.",
        )
    # Дальше обработчики не запускаем, чтобы AI не отвечал на удалённое сообщение
    raise ApplicationHandlerStop
