import os
import json
import urllib.request
import urllib.parse

def validate_token(token):
    """Проверяет токен через API Телеграма"""
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url) as response:
            data = json.loads(response.read().decode())
            if data.get("ok"):
                return data["result"]["username"]
            return None
    except:
        return None

def send_test_message(token, chat_id, text):
    """Отправляет тестовое сообщение для проверки ID канала"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    params = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    try:
        with urllib.request.urlopen(url, data=params) as response:
            return json.loads(response.read().decode()).get("ok")
    except:
        return False

def load_config():
    """Загружает существующий конфиг, если он есть"""
    config = {}
    if os.path.exists("config.txt"):
        with open("config.txt", "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    config[k] = v
    return config

def run_setup():
    print("==================================================")
    print("   МАСТЕР УСТАНОВКИ MITHGOAL40000 VOIGHT-KAMPFF   ")
    print("==================================================")
    
    current_config = load_config()
    new_config = {}

    # --- ШАГ 1: Токен ---
    while True:
        print("\nШАГ 1: Токен бота")
        print("Получите у @BotFather. Формат 123456:ABC-DEF...")
        old_token = current_config.get("API_TOKEN", "")
        prompt = f"Введите токен [{old_token}]: " if old_token else "Введите токен: "
        
        token = input(prompt).strip() or old_token
        if not token:
            print("❌ Токен не может быть пустым.")
            continue

        print("🔍 Проверяю токен...")
        bot_username = validate_token(token)
        if bot_username:
            print(f"✅ Успех! Бот найден: @{bot_username}")
            new_config["API_TOKEN"] = token
            break
        else:
            print("❌ Ошибка: Токен невалидный. Проверьте правильность.")

    # --- ШАГ 2: Контрольный канал ---
    while True:
        print("\nШАГ 2: Контрольный канал (Логи)")
        print("Рекомендуется ПРИВАТНАЯ группа/канал.")
        print("ID можно узнать у @ChatIdInfobot (публичные группы/каналы обычно начинается с -100, обратите внимание). Взаимодействуйте с меню @ChatIdInfobot, чтобы узнать chatid, либо просто форвардните ему тестовое сообщение с контрольного канала.")
        
        old_chan = current_config.get("REPORT_CHAN", "")
        prompt = f"Введите ID канала [{old_chan}]: " if old_chan else "Введите ID канала: "
        chan_id = input(prompt).strip() or old_chan

        if not chan_id:
            print("❌ ID канала не может быть пустым.")
            continue

        print(f"🔍 Проверяю доступ к {chan_id}...")
        test_ok = send_test_message(new_config["API_TOKEN"], chan_id, "🔔 <b>Тест настройки:</b> Бот успешно подключен!")
        if test_ok:
            print("✅ Связь установлена!")
            new_config["REPORT_CHAN"] = chan_id
            break
        else:
            print("❌ Ошибка: Бот не смог отправить сообщение. Сделайте его админом в группе/канале!")

    # --- ШАГ 3: Инфо ---
    print("\nШАГ 3: Права")
    print("Не забудьте дать боту права на УДАЛЕНИЕ СООБЩЕНИЙ в рабочих чатах.")
    input("Нажмите Enter, чтобы продолжить...")

    # --- ШАГ 4: Команда регистрации ---
    old_roll = current_config.get("ROLL_COMMAND", "/roll")
    print(f"\nШАГ 4: Команда для массовой авторегистрации (текущая: {old_roll})")
    roll_cmd = input(f"Введите команду [{old_roll}]: ").strip() or old_roll
    new_config["ROLL_COMMAND"] = roll_cmd if roll_cmd.startswith("/") else "/" + roll_cmd

    # --- ШАГ 5: Кодовое слово ---
    old_code = current_config.get("CODE_WORD", "залупа")
    print(f"\nШАГ 5: Кодовое слово для прохождения теста Войта-Кампфа (текущее: {old_code})")
    new_config["CODE_WORD"] = input(f"Введите слово [{old_code}]: ").strip() or old_code

    # --- ШАГ 6: Видео ---
    old_video = current_config.get("VIDEO_URL", "https://t.me/lpntz/138179")
    print(f"\nШАГ 6: Видео-инструкция для прохождения пользователем теста Войта-Кампфа (текущая: {old_video})")
    new_config["VIDEO_URL"] = input(f"Введите ссылку [{old_video}]: ").strip() or old_video

    # --- ШАГ 7: Порог ---
    old_score = current_config.get("SPAM_THRESHOLD", "7.0")
    print(f"\nШАГ 7: Порог бана, количество спам-очков, по достижению которого тест Войта-Кампфа не уже применяется, а наступает автокарантин (текущий: {old_score})")
    new_config["SPAM_THRESHOLD"] = input(f"Введите баллы [{old_score}]: ").strip() or old_score

    # --- ШАГ 8: Админы ---
    existing_admins = ""
    if os.path.exists("admins.txt"):
        with open("admins.txt", "r") as f:
            existing_admins = ",".join([line.strip() for line in f if line.strip()])
    
    print(f"\nШАГ 8: Администраторы (ID через запятую)")
    print(f"Челы, которым можно копаться в конфигах. Текущие: {existing_admins if existing_admins else 'нет'}")
    admin_input = input("Введите новые ID или Enter чтобы оставить: ").strip()
    
    if admin_input:
        with open("admins.txt", "w", encoding="utf-8") as f:
            for aid in admin_input.split(","):
                f.write(aid.strip() + "\n")

    # --- ШАГ 9-14: Словари ---
    files_to_check = {
        "spam_keywords.txt": ["крипта", "выплаты", "инвестиции"],
        "promo_keywords.txt": ["акция", "скидка", "подпишись"],
        "shorts.txt": ["ку", "привет", "как дела"],
        "spam_messages.txt": ["Я спамер и горжусь этим!", "Мамке своей в ачько спамь"],
        "titles.txt": ["Жопный Крот", "Угрюмая Пидрила"]
    }

    print("\nШАГ 9-14: Проверка словарей")
    for filename, defaults in files_to_check.items():
        if not os.path.exists(filename):
            with open(filename, "w", encoding="utf-8") as f:
                f.write("\n".join(defaults) + "\n")
            print(f" [+] Создан {filename} (дефолт)")
        else:
            with open(filename, "r", encoding="utf-8") as f:
                count = len([l for l in f if l.strip()])
            print(f" [!] {filename} уже содержит {count} записей.")

    # Служебные файлы
    for f_name in ["database_log.txt", "quarantine.txt"]:
        if not os.path.exists(f_name):
            open(f_name, "a").close()

    # Сохраняем конфиг
    with open("config.txt", "w", encoding="utf-8") as f:
        for k, v in new_config.items():
            f.write(f"{k}={v}\n")

    print("\n==================================================")
    print("   КОНФИГУРАЦИЯ СОХРАНЕНА   ")
    print("==================================================")

    # Финальный отчет
    report = (
        "🚀 <b>Система Anti-Spam готова!</b>\n\n"
        "<b>Команды управления:</b>\n"
        "• <code>/zhmykh9</code> — +спам слово\n"
        "• <code>/bzhni17</code> — +промо слово\n"
        "• <code>/hragzz</code> — вывод триггеров из первых двух списков\n"
        "• <code>/lohpidor69</code> — +короткая фраза (характерные словосочетания)\n"
        "• <code>/reset</code> — сброс буфера новых пользователей (новые с момента запуска бота)\n"
        "• <code>/add ID</code> — добавить пользователя в белый список\n"
        "• <code>/remove ID</code> — убрать пользователя из белого списка\n"
        "• <code>/freshmeat</code> — список новых пользователей (с момента запуска)\n"
        "• <code>/addsquad</code> — всех новых в белый список пачкой\n"
        "• <code>/quarantena ID</code> — в карантин\n"
        "• <code>/dequarantena ID</code> — из карантина\n"
        "• <code>/quaralista</code> — список забаненных\n\n"
        f"⚙️ <b>Текущие настройки:</b>\n"
        f"Регистрация: {new_config['ROLL_COMMAND']}\n"
        f"Кодовое слово: {new_config['CODE_WORD']}\n"
        f"Порог: {new_config['SPAM_THRESHOLD']}"
    )
    
    send_test_message(new_config["API_TOKEN"], new_config["REPORT_CHAN"], report)
    print("✅ Финальный отчет отправлен в канал.")
    print("Теперь можно запускать: python main.py")

if __name__ == "__main__":
    run_setup()
