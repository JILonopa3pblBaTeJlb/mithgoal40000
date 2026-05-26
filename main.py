import logging
import re
import time
import random
import asyncio
import unicodedata
import os
from aiogram import Bot, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from difflib import SequenceMatcher
from collections import deque
from pathlib import Path
from aiogram.client.default import DefaultBotProperties

# === Функция загрузки конфига ===
def load_main_config():
    """
    Эта функция открывает файл 'config.txt' и вытаскивает оттуда все настройки:
    токен бота, ID канала для отчетов, секретное слово и т.д.
    Если файла нет, она использует настройки «по умолчанию».
    """
    config = {
        "API_TOKEN": "",
        "REPORT_CHAN": 0,
        "ROLL_COMMAND": "/roll",
        "CODE_WORD": "залупа",
        "VIDEO_URL": "https://t.me/lpntz/138179",
        "SPAM_THRESHOLD": 7.0
    }
    if os.path.exists("config.txt"):
        with open("config.txt", "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    if k in ["REPORT_CHAN"]:
                        config[k] = int(v)
                    elif k in ["SPAM_THRESHOLD"]:
                        config[k] = float(v)
                    else:
                        config[k] = v
    return config

# Загружаем настройки в глобальную переменную CONFIG
CONFIG = load_main_config()

# === Токены и пути к файлам ===
# Здесь мы определяем, где лежат списки спамеров, админов и белые списки.
API_TOKEN = CONFIG["API_TOKEN"]
report_chan = CONFIG["REPORT_CHAN"]
SPAM_MESSAGES_PATH = "spam_messages.txt"
SPAM_WORDS_PATH = "spam_keywords.txt"
PROMO_WORDS_PATH = "promo_keywords.txt"
ADMINS_PATH = "admins.txt"
DATABASE_LOG_PATH = "database_log.txt"
SHORT_MESSAGES_PATH = "shorts.txt"
QUARANTINE_PATH = "quarantine.txt"

# Разрешенные наборы символов (Кириллица, Латиница, Иврит).
# Нужно, чтобы отличать нормальный текст от странных иероглифов.
ALLOWED_RANGES = [
    (0x0400, 0x04FF),  # Кириллица
    (0x0041, 0x005A),  # Latin A-Z
    (0x0061, 0x007A),  # Latin a-z
    (0x0590, 0x05FF),  # Hebrew
]

# Символы пунктуации, которые спамеры часто используют внутри слов (например: п.р.и.в.е.т)
INTERNAL_PUNCT = {
    "-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015",
    "\u00AD", "·", "•", "•", "·", "’", "‘", "'", "`", "´",
    "ㆍ", "。", "·", "•", ".", ",", ":", ";"
}

# === Настройки теста Войта-Кампфа ===
CODE_WORD = CONFIG["CODE_WORD"] # Слово, которое юзер должен написать, чтобы доказать, что он человек
TEST_TIMEOUT = 180              # Сколько секунд дается на ввод слова
SUCCESS_MSG_LIFETIME = 180      # Сколько висит сообщение об успехе перед удалением

# В этом словаре хранятся данные тех, кто прямо сейчас проходит проверку
# user_id -> { "bot_msg_id": id сообщения бота, "task": фоновая задача таймера и т.д. }
pending_tests = {}

async def fail_voight_test(user_id: int, username: str, chat_info: str):
    """
    Вызывается автоматически, если время на тест вышло (180 сек), а кодовое слово не введено.
    Действие: отправляет юзера в карантин и удаляет его сообщения.
    """
    if user_id not in pending_tests:
        return
    
    data = pending_tests.pop(user_id)
    chat_id = data["chat_id"]
    
    # Если юзера еще нет в карантине — добавляем его туда и в файл
    if user_id not in quarantine_ids:
        save_word(QUARANTINE_PATH, str(user_id))
        quarantine_ids.add(user_id)
        # Если он был в белом списке (мало ли), убираем его оттуда
        if user_id in known_user_ids:
            remove_known_user_id(user_id)
            known_user_ids.discard(user_id)

    # Собираем список сообщений, которые надо подтереть за неудачником
    ids_to_delete = [data["bot_msg_id"], data["initial_msg_id"]] + data["messages"]
    
    for m_id in ids_to_delete:
        try:
            await bot.delete_message(chat_id, m_id)
        except:
            pass # Если сообщение уже удалено, просто игнорируем ошибку
            
    # Пишем отчет в админский канал
    await send_log(f"💀 <b>ПРОВАЛ ТЕСТА</b>\n👤 Юзер: @{username} ({user_id})\n🏠 Чат: {chat_info}\n🚫 Статус: Отправлен в карантин")

async def success_voight_test(user_id: int, correct_msg_id: int, username: str, chat_info: str):
    """
    Вызывается, когда юзер ввел правильное кодовое слово.
    Действие: добавляет в белый список и разрешает писать.
    """
    if user_id not in pending_tests:
        return
        
    data = pending_tests.pop(user_id)
    # Останавливаем таймер «провала», так как человек справился
    if not data["task"].done():
        data["task"].cancel()
        
    # Сохраняем в базу «своих»
    save_known_user_id(user_id)
    known_user_ids.add(user_id)
    
    try:
        success_text = "Вам разрешено срать в каменты, господин человек"
        # Меняем текст проверки на поздравительный
        await bot.edit_message_text(
            text=success_text,
            chat_id=data["chat_id"],
            message_id=data["bot_msg_id"]
        )
        
        # Ждем немного и удаляем следы проверки, чтобы не засорять чат
        await asyncio.sleep(SUCCESS_MSG_LIFETIME)
        
        try: await bot.delete_message(data["chat_id"], data["bot_msg_id"])
        except: pass
        try: await bot.delete_message(data["chat_id"], correct_msg_id)
        except: pass
            
    except Exception as e:
        print(f"⚠️ Ошибка в финале теста: {e}")
        
    await send_log(f"👤 <b>ТЕСТ ПРОЙДЕН</b>\n👤 Юзер: @{username} ({user_id})\n🏠 Чат: {chat_info}\n✅ Статус: Добавлен в WhiteList")

def load_quarantine_ids(path=QUARANTINE_PATH):
    """Читает файл карантина и превращает его в список ID в памяти бота."""
    q_ids = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        q_ids.add(int(line.strip()))
                    except:
                        continue
    except FileNotFoundError:
        open(path, "a").close() # Создает файл, если его нет
    return q_ids

# Загружаем список «плохих парней» при старте
quarantine_ids = load_quarantine_ids()
# Буфер для новых пользователей, которых бот видит впервые
freshmeat_buffer = {}
router = Router()

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

def remove_quarantine_id(user_id: int):
    """Вычеркивает пользователя из файла карантина."""
    try:
        with open(QUARANTINE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(QUARANTINE_PATH, "w", encoding="utf-8") as f:
            for line in lines:
                if line.strip() != str(user_id):
                    f.write(line)
    except FileNotFoundError:
        pass

def load_list(filename):
    """Универсальная функция: читает файл построчно и делает из него набор уникальных фраз."""
    if not os.path.exists(filename):
        open(filename, "a").close()
        return set()
    with open(filename, encoding="utf-8") as f:
        return set(line.strip().lower() for line in f if line.strip())

short_messages = load_list(SHORT_MESSAGES_PATH)

def save_word(path, word):
    """Просто дописывает слово или ID в указанный текстовый файл."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(word + "\n")

def load_known_user_ids(path=DATABASE_LOG_PATH):
    """Загружает список ID «безопасных» пользователей из лог-файла базы."""
    known_ids = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(":")
                if len(parts) >= 3:
                    try:
                        known_ids.add(int(parts[1]))
                    except:
                        continue
    except FileNotFoundError:
        pass
    return known_ids

def save_known_user_id(user_id: int):
    """Записывает в лог, что пользователь прошел проверку и теперь в белом списке."""
    with open(DATABASE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"manual:{user_id}:added_by_admin\n")

def remove_known_user_id(user_id: int):
    """Удаляет пользователя из белого списка (если админ решил его забанить)."""
    try:
        with open(DATABASE_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(DATABASE_LOG_PATH, "w", encoding="utf-8") as f:
            for line in lines:
                if f":{user_id}:" not in line:
                    f.write(line)
    except FileNotFoundError:
        pass

# Инициализируем все списки из файлов
known_user_ids = load_known_user_ids()
fake_spam_messages = load_list(SPAM_MESSAGES_PATH)
spam_keywords = load_list(SPAM_WORDS_PATH)
promo_keywords = load_list(PROMO_WORDS_PATH)
admin_ids = set(map(int, load_list(ADMINS_PATH)))

# История последних сообщений для борьбы с повторами (дублями)
RECENT_MESSAGES = deque(maxlen=200)
SPAM_THRESHOLD = CONFIG["SPAM_THRESHOLD"]
NEW_USERS = dict()

async def send_log(text):
    """Отправляет служебные уведомления в специальный админский канал."""
    if not report_chan:
        return
    try:
        await bot.send_message(chat_id=report_chan, text=text)
        await asyncio.sleep(0.5) # Защита от спама самого Телеграма
    except Exception as e:
        print(f"⚠️ Не удалось отправить лог: {e}")

def is_admin(user_id):
    """Проверяет, является ли пользователь админом бота."""
    return user_id in admin_ids

def is_emoji_only(text: str) -> bool:
    """Проверяет, состоит ли сообщение только из смайликов."""
    emoji_pattern = re.compile(r'^[\U0001F000-\U0001FFFF\s]+$')
    return bool(emoji_pattern.match(text))
    
def is_allowed_letter(ch: str) -> bool:
    """
    Проверяет, относится ли символ к разрешенному алфавиту (русский, английский, иврит).
    Нужно, чтобы отсекать арабскую вязь или странные символы, которыми спамеры маскируют текст.
    """
    if not ch:
        return False
    cat = unicodedata.category(ch)
    if not cat.startswith("L"): # 'L' означает Letter (Буква)
        return False
    cp = ord(ch)
    for start, end in ALLOWED_RANGES:
        if start <= cp <= end:
            return True
    return False

def has_obfuscation(text: str) -> bool:
    """
    Детектор хитростей. Проверяет, не пытается ли юзер скрыть спам:
    - Невидимыми символами
    - Смешиванием алфавитов
    - Точками внутри слов (п.р.и.в.е.т)
    """
    if not text:
        return False
    s = unicodedata.normalize("NFKC", text)
    for ch in s:
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Cc"): # Скрытые символы форматирования
            return True
    for ch in s:
        if unicodedata.category(ch) == "Mn": # Модификаторы букв (ударения и т.д.)
            return True
    for ch in s:
        # Если буква не входит в наш разрешенный список алфавитов
        if unicodedata.category(ch).startswith("L") and not is_allowed_letter(ch):
            return True
    
    # Ищем точки и тире внутри слов
    tokens = re.findall(r'\S+', s)
    for token in tokens:
        if len(token) < 3:
            continue
        for i in range(1, len(token) - 1):
            prev_ch = token[i - 1]
            mid_ch = token[i]
            next_ch = token[i + 1]
            if mid_ch in INTERNAL_PUNCT and is_allowed_letter(prev_ch) and is_allowed_letter(next_ch):
                return True
            if (not is_allowed_letter(mid_ch)) and (not mid_ch.isdigit()) and is_allowed_letter(prev_ch) and is_allowed_letter(next_ch):
                return True
        # Считаем количество переходов с букв на не-буквы внутри слова
        transitions = 0
        for i in range(len(token) - 2):
            if is_allowed_letter(token[i]) and (not is_allowed_letter(token[i + 1]) and not token[i+1].isspace()) and is_allowed_letter(token[i + 2]):
                transitions += 1
                if transitions >= 1:
                    return True
    return False
    
def load_titles(path="titles.txt"):
    """Загружает список обзывательств, которыми бот будет заменять имена спамеров."""
    try:
        with open(path, encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and len(line.strip()) <= 50]
    except FileNotFoundError:
        return ["Жопный крот", "Мамке своей в ачько спамь", "Угрюмая Пидрила"]

titles_list = load_titles()

def replace_username_with_title(username: str) -> str:
    """Выбирает случайное обидное прозвище для спамера."""
    if not titles_list:
        return username or "Unknown"
    new_name = random.choice(titles_list)
    return f"{new_name}" if username else new_name

def check_spam(user_id: int, username: str, text: str, timestamp: float, chat_id: int) -> tuple[float, str]:
    """
    ГЛАВНЫЙ МОЗГ: Анализирует сообщение и начисляет штрафные баллы (score).
    Если баллов больше SPAM_THRESHOLD, сообщение считается спамом.
    """
    score = 0
    log_lines = []
    lowered = text.lower()
    repeats_from_same_user = 0
    
    # Проверка на маскировку текста
    if has_obfuscation(text):
        score += 6
        log_lines.append("🕵️ Обнаружена обфускация текста (невидимые символы): +6")
        
    # Сверяем со списком запрещенных коротких фраз
    if lowered in short_messages:
        score += 6
        log_lines.append("📜 Совпадение с фразой из short_messages: +6")
        
    # Проверка на дубликаты в чате
    for msg in RECENT_MESSAGES:
        if timestamp - msg['timestamp'] <= 90 and msg['chat_id'] == chat_id:
            # Сравниваем похожесть текста (0.95 = почти идентичны)
            sim = SequenceMatcher(None, text, msg['text']).ratio()
            if sim >= 0.95:
                if msg['user_id'] == user_id:
                    repeats_from_same_user += 1
                else:
                    score += 3
                    log_lines.append("🔁 Повтор чужого сообщения: +3")
                    break
                    
    # Если юзер частит одним и тем же сообщением
    if repeats_from_same_user == 1:
        score += 6
        log_lines.append("🔁 Повтор от того же юзера (1 раз): +6")
    elif repeats_from_same_user >= 2:
        score += 6
        log_lines.append(f"🔁 Множественный повтор ({repeats_from_same_user} раз): +6")
        
    # Поиск ключевых слов спама
    if any(kw in lowered for kw in spam_keywords):
        score += 4
        log_lines.append("🧠 Ключевые слова из спам-словаря: +4")
        
    # Ищем упоминания денег (суммы + валюта)
    if re.search(r"(?:\b|от\s*)\+?\d{1,7}(?:[,.]\d{3})?(?:\s*(?:р|₽|руб|рублей|к|k|\$)|(?:р|₽|руб|рублей|к|k|\$))?\b", lowered):
        score += 2
        log_lines.append("💸 Найдена сумма с валютой или 'к'/'$': +2")
        
    # Специфический паттерн «нужна помощь + деньги»
    if "нужна помощь" in lowered and re.search(r"(?:\b|от\s*)\+?\d{1,7}(?:[,.]\d{3})?(?:\s*(?:р|₽|руб|к|k|\$)|(?:р|₽|руб|к|k|\$))?\b", lowered):
        score += 2
        log_lines.append("🧠 Шаблон 'нужна помощь + сумма': +2")
        
    # Призывы писать в личку
    if re.search(r"\b(в лс|в личк|в личн|пиши мне|не стесня)\b", lowered):
        score += 1
        log_lines.append("📨 Приглашение в личку: +1")
        
    # Длинные сообщения подозрительны для новых юзеров
    if len(text) >= 50:
        score += 0.5
        log_lines.append("📏 Длинное сообщение (50+ символов): +0.5")
        
    # Ссылки
    if "http" in lowered or "t.me/" in lowered:
        score += 1.5
        log_lines.append("🔗 Ссылка: +1.5")
        
    # Смешивание кириллицы и латиницы (типично для спама)
    if re.search(r'[a-zA-Z].*[а-яА-ЯёЁ]|[а-яА-ЯёЁ].*[a-zA-Z]', text):
        score += 1.5
        log_lines.append("👀 Смешение кириллицы и латиницы: +1.5")
        
    # Промо-слова
    if any(kw in lowered for kw in promo_keywords):
        score += 2
        log_lines.append("🎯 Ключевое слово из промо-словаря: +2")
        
    log_lines.append(f"📊 Итоговая оценка: {score:.1f}")
    return score, "\n".join(log_lines)

# === Секция Админских команд ===
# Команды позволяют добавлять слова и управлять пользователями прямо из Телеграма

@router.message(Command("zhmykh9"))
async def cmd_zhmykh9(msg: types.Message):
    """Админская команда для добавления нового спам-слова в словарь."""
    if not is_admin(msg.from_user.id):
        return
    word = msg.text[len("/zhmykh9"):].strip().lower()
    if not word:
        await msg.reply("⚠️ Укажи слово для добавления в спам.")
        return
    save_word(SPAM_WORDS_PATH, word)
    spam_keywords.add(word)
    await msg.reply(f"✅ Добавлено в spam_keywords: {word}")

@router.message(Command("bzhni17"))
async def cmd_bzhni17(msg: types.Message):
    """Админская команда для добавления промо-слова."""
    if not is_admin(msg.from_user.id):
        return
    word = msg.text[len("/bzhni17"):].strip().lower()
    if not word:
        await msg.reply("⚠️ Укажи слово для добавления в промо.")
        return
    save_word(PROMO_WORDS_PATH, word)
    promo_keywords.add(word)
    await msg.reply(f"✅ Добавлено в promo_keywords: {word}")

@router.message(Command("hragzz"))
async def cmd_hragzz(msg: types.Message):
    """Показывает текущие списки запрещенных слов."""
    if not is_admin(msg.from_user.id):
        return
    spam_list = "\n".join(spam_keywords) or "—"
    promo_list = "\n".join(promo_keywords) or "—"
    await msg.reply(f"📚 Ключевые слова:\n\n🧠 Спам:\n{spam_list}\n\n🎯 Промо:\n{promo_list}")

@router.message(Command("reset"))
async def cmd_reset(msg: types.Message):
    """Очищает временный буфер новых пользователей."""
    if not is_admin(msg.from_user.id):
        return
    count = len(freshmeat_buffer)
    freshmeat_buffer.clear()
    await msg.reply(f"♻️ Сброшено freshmeat: {count} записей.")

@router.message(Command("add"))
async def cmd_add(msg: types.Message):
    """Вручную добавляет ID пользователя в белый список."""
    if not is_admin(msg.from_user.id):
        return
    try:
        uid = int(msg.text[len("/add"):].strip())
        if uid in known_user_ids:
            await send_log(f"ℹ️ /add: {uid} уже есть в базе.")
        else:
            save_known_user_id(uid)
            known_user_ids.add(uid)
            await send_log(f"✅ Добавлен в known_user_ids: {uid}")
    except:
        await send_log("⚠️ Не удалось добавить — неверный формат ID")

@router.message(Command("remove"))
async def cmd_remove(msg: types.Message):
    """Удаляет ID пользователя из белого списка."""
    if not is_admin(msg.from_user.id):
        return
    try:
        uid = int(msg.text[len("/remove"):].strip())
        remove_known_user_id(uid)
        known_user_ids.discard(uid)
        await send_log(f"➖ Удалён из known_user_ids: {uid}")
    except:
        await send_log("⚠️ Не удалось удалить — неверный формат ID")

@router.message(Command("freshmeat"))
async def cmd_freshmeat(msg: types.Message):
    """Показывает список «новичков», которые писали в чат за последнее время."""
    if not is_admin(msg.from_user.id):
        return
    report_lines = []
    for uid, data in freshmeat_buffer.items():
        username = data['username']
        text = data['text']
        is_spam = data['spam']
        emoji = "🚨" if is_spam else "✅"
        report_lines.append(f"{emoji} {username} ({uid}): {text}")
    if not report_lines:
        await msg.reply("ℹ️ Буфер freshmeat пуст.")
    else:
        await msg.reply("🤡 Fresh meat:\n" + "\n".join(report_lines))

@router.message(Command("addsquad"))
async def add_squard(msg: types.Message):
    """Массово добавляет всех новичков из буфера в белый список."""
    if not is_admin(msg.from_user.id):
        return
    if not freshmeat_buffer:
        await send_log("ℹ️ Буфер freshmeat пуст, нечего добавлять.")
        return
    added = 0
    skipped = []
    for user_id in freshmeat_buffer.keys():
        if user_id not in known_user_ids:
            try:
                with open(DATABASE_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(f"unknown:{user_id}:added_by_addsquad\n")
                known_user_ids.add(user_id)
                added += 1
            except Exception as e:
                await send_log(f"⚠️ Ошибка при добавлении {user_id}: {e}")
        else:
            skipped.append(user_id)
    await send_log(f"✅ Добавлено {added} пользователей из freshmeat.")
    if skipped:
        await send_log(f"ℹ️ Пропущено {len(skipped)} — уже были в базе.")
    freshmeat_buffer.clear()

@router.message(Command("lohpidor69"))
async def cmd_lohpidor69(msg: types.Message):
    """Добавляет короткую фразу-триггер (например, «привет, как дела»)."""
    if not is_admin(msg.from_user.id):
        return
    phrase = msg.text[len("/lohpidor69"):].strip().lower()
    if not phrase:
        await msg.reply("⚠️ Укажи короткую фразу!")
        return
    if phrase in short_messages:
        await msg.reply(f"⚠️ Фраза '{phrase}' уже есть!")
        return
    save_word(SHORT_MESSAGES_PATH, phrase)
    short_messages.add(phrase)
    await msg.reply(f"✅ Фраза '{phrase}' добавлена!")
    
@router.message(Command("quarantena"))
async def cmd_quarantena(msg: types.Message):
    """Ручной перевод пользователя в режим карантина (бана)."""
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("⚠️ Укажи ID пользователя.")
        return
    try:
        target_id = int(parts[1])
        if target_id in quarantine_ids:
            await msg.reply(f"ℹ️ Юзер {target_id} уже в карантине.")
            return
        save_word(QUARANTINE_PATH, str(target_id))
        quarantine_ids.add(target_id)
        if target_id in known_user_ids:
            remove_known_user_id(target_id)
            known_user_ids.discard(target_id)
        await send_log(f"☣️ Юзер {target_id} в КАРАНТИНЕ.")
        await msg.reply(f"✅ Юзер {target_id} в карантине.")
    except:
        await msg.reply("⚠️ Ошибка ID.")
        
@router.message(Command("quaralista"))
async def cmd_quaralista(msg: types.Message):
    """Показывает список всех, кто сейчас находится в карантине."""
    if not is_admin(msg.from_user.id):
        return
    if not quarantine_ids:
        await msg.reply("📜 Список карантина пуст.")
        return
    list_str = "\n".join([f"• <code>{uid}</code>" for uid in quarantine_ids])
    await msg.reply(f"☣️ <b>Карантин:</b>\n\n{list_str}")
    
@router.message(Command("dequarantena"))
async def cmd_dequarantena(msg: types.Message):
    """Выпускает пользователя из карантина (разбанивает)."""
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2:
        await msg.reply("⚠️ Укажи ID.")
        return
    try:
        target_id = int(parts[1])
        if target_id not in quarantine_ids:
            await msg.reply(f"ℹ️ Юзер {target_id} не в карантине.")
            return
        remove_quarantine_id(target_id)
        quarantine_ids.discard(target_id)
        await send_log(f"💊 Юзер {target_id} ВЫЛЕЧЕН.")
        await msg.reply(f"✅ Юзер {target_id} извлечен.")
    except:
        await msg.reply("⚠️ Ошибка ID.")

# === ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ ===

@router.message()
async def handle_msg(msg: types.Message):
    """
    Эта функция вызывается при КАЖДОМ новом сообщении в чате.
    Здесь происходит вся магия фильтрации.
    """
    user_id = msg.from_user.id
    
    # === 0. Игнорирование служебных аккаунтов и самого себя ===
    try:
        # 777000 - Телеграм, 1087968824 - Анонимный админ
        if user_id in [777000, 1087968824] or user_id == bot.id:
            return
    except:
        pass

    username = msg.from_user.username or msg.from_user.full_name
    chat_id = msg.chat.id
    chat_handle = f"@{msg.chat.username}" if msg.chat.username else msg.chat.title
    chat_info = f"{chat_handle} (<code>{chat_id}</code>)"
    
    timestamp = time.time()
    
    content_type = msg.content_type
    text = (msg.text or msg.caption or "").strip()
    content_desc = text[:100] if text else f"[{content_type.upper()}]"

    # === 1. Обработка РОЛЛА (Добровольная регистрация) ===
    if text.lower().startswith(CONFIG["ROLL_COMMAND"].lower()):
        if user_id not in known_user_ids and user_id not in quarantine_ids:
            save_known_user_id(user_id)
            known_user_ids.add(user_id)
            await send_log(f"🎲 <b>REGISTRATION</b>\n👤 @{username} (<code>{user_id}</code>)\n🏠 {chat_info}\n📝 Способ: {CONFIG['ROLL_COMMAND']}")
        return

    # === 2. Проверка на КАРАНТИН ===
    # Если юзер в списке плохих, его сообщение удаляется, а в чат пишется «фейковый спам»
    if user_id in quarantine_ids:
        freshmeat_buffer[user_id] = {'username': username, 'text': content_desc, 'spam': True}
        fake_msg_body = random.choice(list(fake_spam_messages)) if fake_spam_messages else "Сообщение удалено"
        display_name = replace_username_with_title(username)
        fake_msg = f"🚨SPAM!: 🤡@{display_name} ({user_id}):\n📨{fake_msg_body}"
        try:
            await bot.send_message(chat_id=chat_id, text=fake_msg, reply_to_message_id=msg.message_id)
            await asyncio.sleep(0.5)
            if msg.chat.type != 'private':
                await msg.delete() # Удаляем оригинал сообщения спамера
        except Exception as e:
            if "migrated to a supergroup" not in str(e):
                print(f"⚠️ Ошибка карантина: {e}")
        
        await send_log(f"🚨 <b>QUARANTINE BLOCK</b>\n👤 @{username} (<code>{user_id}</code>)\n🏠 {chat_info}\n📝 {content_desc}")
        return

    # === 3. Если юзер в процессе прохождения ТЕСТА ВОЙТА-КАМПФА ===
    if user_id in pending_tests:
        # Проверяем, ввел ли он заветное слово
        if text.lower() == CODE_WORD.lower():
            asyncio.create_task(success_voight_test(user_id, msg.message_id, username, chat_info))
        else:
            # Складываем его сообщения в список, чтобы потом всё разом удалить
            pending_tests[user_id]["messages"].append(msg.message_id)
        return

    # === 4. Игнорируем сообщения, состоящие только из ЭМОДЗИ ===
    if text and is_emoji_only(text):
        if user_id not in known_user_ids:
            freshmeat_buffer[user_id] = {'username': username, 'text': text, 'spam': False}
        return

    # === 5. Пропускаем ИЗВЕСТНЫХ юзеров (Белый список) ===
    if (user_id in known_user_ids or is_admin(user_id) or user_id == 777000) and msg.chat.type != 'private':
        if text:
            await send_log(f"✅ <b>WHITE LIST</b>\n👤 @{username} (<code>{user_id}</code>)\n🏠 {chat_info}\n📝 {text[:150]}")
        # Сохраняем сообщение в историю для контроля дублей
        RECENT_MESSAGES.append({
            'chat_id': chat_id, 'message_id': msg.message_id,
            'user_id': user_id, 'text': text, 'timestamp': timestamp
        })
        # Чистим старую историю (старше 2 минут)
        while RECENT_MESSAGES and timestamp - RECENT_MESSAGES[0]['timestamp'] > 120:
            RECENT_MESSAGES.popleft()
        return

    # === 6. НОВЫЙ ПОЛЬЗОВАТЕЛЬ (Анализ на спам) ===
    freshmeat_buffer[user_id] = {'username': username, 'text': content_desc, 'spam': False}
    score = 0
    decision_log = "Нет текста"
    if text:
        score, decision_log = check_spam(user_id, username, text, timestamp, chat_id)
    
    # Проверка на скорострельность (флуд)
    repeat_count = len([
        m for m in RECENT_MESSAGES
        if m['user_id'] == user_id and m['chat_id'] == chat_id and (timestamp - m['timestamp'] <= 3)
    ])

    # Если баллов слишком много или юзер флудит — сразу в карантин без тестов
    if score > SPAM_THRESHOLD or repeat_count >= 1:
        freshmeat_buffer[user_id]['spam'] = True
        save_word(QUARANTINE_PATH, str(user_id))
        quarantine_ids.add(user_id)
        fake_msg_body = random.choice(list(fake_spam_messages)) if fake_spam_messages else "Удалено"
        display_name = replace_username_with_title(username)
        fake_msg = f"🚨SPAM!:  clowns@{display_name} ({user_id}):\n📨{fake_msg_body}"
        try:
            await bot.send_message(chat_id=chat_id, text=fake_msg, reply_to_message_id=msg.message_id)
            if msg.chat.type != 'private':
                await msg.delete()
        except:
            pass
        await send_log(f"🔥 <b>AUTO-QUARANTINE</b>\n👤 @{username} (<code>{user_id}</code>)\n🏠 {chat_info}\n📊 Score: {score}\n📋 {decision_log}")
        return
    else:
        # ЕСЛИ СОМНЕВАЕМСЯ — ЗАПУСКАЕМ ТЕСТ ВОЙТА-КАМПФА
        test_text = (
            f"{CONFIG['VIDEO_URL']}\n\n"
            "Вам предложено пройти тест Войта-Кампфа. Инструкции в видео. "
            "У вас осталось 180 секунд для выполнения."
        )
        try:
            bot_msg = await msg.reply(test_text, disable_web_page_preview=False)
            loop = asyncio.get_event_loop()
            # Создаем таймер на 180 секунд
            task = loop.create_task(asyncio.sleep(TEST_TIMEOUT))
            pending_tests[user_id] = {
                "bot_msg_id": bot_msg.message_id,
                "initial_msg_id": msg.message_id,
                "chat_id": chat_id,
                "messages": [],
                "task": task
            }
            # Если таймер истек сам по себе — запускаем функцию провала теста
            def check_done(t):
                if not t.cancelled():
                    asyncio.create_task(fail_voight_test(user_id, username, chat_info))
            task.add_done_callback(check_done)
            
            await send_log(f"🔍 <b>START VOIGHT TEST</b>\n👤 @{username} (<code>{user_id}</code>)\n🏠 {chat_info}\n📊 Score: {score}")
        except Exception as e:
            if "migrated to a supergroup" not in str(e):
                print(f"⚠️ Ошибка старта теста: {e}")

    # Добавляем сообщение в историю
    RECENT_MESSAGES.append({
        'chat_id': chat_id, 'message_id': msg.message_id,
        'user_id': user_id, 'text': text, 'timestamp': timestamp
    })
    while RECENT_MESSAGES and timestamp - RECENT_MESSAGES[0]['timestamp'] > 120:
        RECENT_MESSAGES.popleft()
        
if __name__ == '__main__':
    """Точка входа в программу."""
    logging.basicConfig(level=logging.WARNING)
    from aiogram import Dispatcher
    
    async def main():
        if not API_TOKEN:
            print("❌ Ошибка: API_TOKEN не найден. Запустите setup.py")
            return
        
        # Проверяем, живой ли токен и кто мы такие
        me = await bot.get_me()
        print(f"✅ Бот запущен: @{me.username} (ID: {me.id})")
        
        dp = Dispatcher()
        dp.include_router(router)
        # Запускаем бесконечное прослушивание сообщений
        await dp.start_polling(bot, skip_updates=True)

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен")
