# -*- coding: utf-8 -*-
import os
import time
import threading
import logging
import requests
from collections import defaultdict
from datetime import datetime
from flask import Flask, request, jsonify
import telebot
from telebot import types

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_TOKEN = os.getenv("API_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_ID = os.getenv("ADMIN_ID")

if not all([BOT_TOKEN, API_TOKEN]):
    raise RuntimeError("❌ Missing BOT_TOKEN or API_TOKEN")

BASE_URL = "https://api.viotp.com"
USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"
PROXY_URL = os.getenv("PROXY_URL")
USE_POLLING = os.getenv("USE_POLLING", "false").lower() == "true"

# ==================== LOGGING ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ==================== FLASK & BOT ====================
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
bot = telebot.TeleBot(BOT_TOKEN, threaded=True, num_threads=10)

# ==================== STORAGE ====================
user_orders = defaultdict(dict)
active_checks = {}

# ==================== SERVICES ====================
SERVICES = {
    'okvip1': {'id': '687', 'name': 'Saibo88'},
    'okvip2': {'id': '733', 'name': 'Xm288'}
}

COUNTRY = "vn"

NETWORKS = {
    'any': '🎲 Bất kỳ',
    'MOBIFONE': '📱 Mobifone',
    'VINAPHONE': '📞 Vinaphone', 
    'VIETTEL': '📶 Viettel',
    'VIETNAMOBILE': '🔵 Vietnamobile',
    'ITELECOM': '🟢 ITelecom',
    'WINTEL': '📡 Wintel',
}

# ==================== HTTP SESSION ====================
session = requests.Session()
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(pool_connections=20, pool_maxsize=40, max_retries=retry_strategy)
session.mount('https://', adapter)
session.mount('http://', adapter)

if USE_PROXY and PROXY_URL:
    session.proxies = {'http': PROXY_URL, 'https': PROXY_URL}
    logger.info("✅ Proxy enabled")

session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

# ==================== API FUNCTIONS ====================
def api_call(endpoint, params=None):
    try:
        if not params:
            params = {}
        params['token'] = API_TOKEN
        
        url = f"{BASE_URL}/{endpoint}"
        response = session.get(url, params=params, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"API Error ({endpoint}): {e}")
        return {"status_code": -1, "message": str(e)}

def get_balance():
    result = api_call("users/balance")
    if result.get("status_code") == 200:
        return {"status": 1, "balance": result.get("data", {}).get("balance", 0)}
    return {"status": 0, "message": "Không lấy được số dư"}

def create_order(service_id, network=None):
    params = {"serviceId": service_id, "country": COUNTRY}
    
    if network and network != 'any':
        params['network'] = network
    
    result = api_call("request/getv2", params)
    
    if result.get("status_code") == 200:
        data = result.get("data", {})
        return {
            "status": 1,
            "id": data.get("request_id"),
            "phone": data.get("phone_number"),
            "balance": data.get("balance", 0)
        }
    elif result.get("status_code") == -2:
        return {"status": 0, "message": "Số dư không đủ"}
    elif result.get("status_code") == -3:
        return {"status": 0, "message": "Kho số tạm hết"}
    elif result.get("status_code") == -4:
        return {"status": 0, "message": "Dịch vụ không khả dụng"}
    elif result.get("status_code") == 429:
        return {"status": 0, "message": "Vượt quá giới hạn"}
    else:
        return {"status": 0, "message": result.get("message", "Lỗi không xác định")}

def check_order(request_id):
    result = api_call("session/getv2", {"requestId": request_id})
    
    if result.get("status_code") == 200:
        data = result.get("data", {})
        status = data.get("Status", 0)
        
        if status == 1:
            return {
                "status": 1,
                "code": data.get("Code"),
                "is_sound": data.get("IsSound", "false") == "true"
            }
        elif status == 0:
            return {"status": 1, "code": None, "waiting": True}
        elif status == 2:
            return {"status": 0, "message": "Hết thời gian"}
    
    return {"status": 0, "message": "Lỗi kiểm tra OTP"}

# ==================== AUTO CHECK OTP ====================
def auto_check_otp(chat_id, request_id, phone, service_name, network_name):
    key = f"{chat_id}_{request_id}"
    if key in active_checks:
        return
    active_checks[key] = True
    try:
        for _ in range(120):
            time.sleep(3)
            result = check_order(request_id)
            if result.get("status") == 1 and result.get("code"):
                code = result["code"]
                is_sound = result.get("is_sound", False)
                
                msg = (
                    f"✅ <b>OTP ĐÃ VỀ!</b>\n\n"
                    f"🎰 <b>OKVIP</b>\n"
                    f"📞 <b>Số:</b> <code>{phone}</code>\n"
                    f"📶 <b>Nhà mạng:</b> {network_name}\n\n"
                    f"🔑 <b>MÃ OTP:</b> <code>{code}</code>\n\n"
                    f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                )
                
                if is_sound:
                    msg += f"\n📞 <i>(Nhận qua cuộc gọi)</i>"
                
                bot.send_message(chat_id, msg, parse_mode="HTML")
                user_orders[chat_id][request_id]['status'] = 'completed'
                user_orders[chat_id][request_id]['otp'] = code
                break
            elif result.get("status") == 0:
                bot.send_message(chat_id, 
                    f"⏰ <b>HẾT THỜI GIAN CHỜ OTP</b>\n\n"
                    f"🎰 <b>OKVIP</b>\n"
                    f"📞 <b>Số:</b> <code>{phone}</code>\n"
                    f"📶 <b>Nhà mạng:</b> {network_name}",
                    parse_mode="HTML"
                )
                user_orders[chat_id][request_id]['status'] = 'timeout'
                break
    except Exception as e:
        logger.error(f"Auto check error: {e}")
    finally:
        active_checks.pop(key, None)

# ==================== KEYBOARDS ====================
def get_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.row(
        types.KeyboardButton("📱 OKVIP1"),
        types.KeyboardButton("📱 OKVIP2")
    )
    kb.row(
        types.KeyboardButton("📦 Đơn hàng"),
        types.KeyboardButton("❓ Hướng dẫn")
    )
    return kb

def get_network_keyboard(service_key):
    kb = types.InlineKeyboardMarkup(row_width=2)
    
    for network_code, network_name in NETWORKS.items():
        kb.add(types.InlineKeyboardButton(
            network_name,
            callback_data=f"rent_{service_key}_{network_code}"
        ))
    
    return kb

# ==================== BOT HANDLERS ====================
@bot.message_handler(commands=['start'])
def cmd_start(message):
    text = (
        "✨ <b>CHÀO MỪNG ĐẾN OKVIP BOT</b>\n\n"
        "🎰 Thuê số OTP tự động\n"
        "⚡ Nhanh chóng - Tiện lợi\n\n"
        "👇 <b>Chọn dịch vụ:</b>"
    )
    bot.send_message(message.chat.id, text, reply_markup=get_main_keyboard(), parse_mode="HTML")
    logger.info(f"User {message.chat.id} started bot")

@bot.message_handler(func=lambda m: m.text in ["📱 OKVIP1", "OKVIP1"])
def cmd_okvip1(message):
    text = (
        f"🎰 <b>OKVIP</b>\n\n"
        f"📶 <b>Chọn nhà mạng:</b>"
    )
    bot.send_message(message.chat.id, text, reply_markup=get_network_keyboard('okvip1'), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in ["📱 OKVIP2", "OKVIP2"])
def cmd_okvip2(message):
    text = (
        f"🎰 <b>OKVIP</b>\n\n"
        f"📶 <b>Chọn nhà mạng:</b>"
    )
    bot.send_message(message.chat.id, text, reply_markup=get_network_keyboard('okvip2'), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in ["📦 Đơn hàng"])
def cmd_orders(message):
    orders = user_orders.get(message.chat.id)
    if not orders:
        bot.reply_to(message, "📭 <b>Chưa có đơn hàng</b>", parse_mode="HTML")
        return
    
    recent = list(orders.items())[-10:]
    recent.reverse()
    
    text = "📋 <b>ĐƠN HÀNG</b>\n\n"
    
    for req_id, info in recent:
        status = info.get('status', 'unknown')
        icon = {'completed': '✅', 'waiting': '⏳', 'timeout': '⌛'}.get(status, '❓')
        status_text = {'completed': 'Đã nhận OTP', 'waiting': 'Đang chờ', 'timeout': 'Hết hạn'}.get(status, 'Không rõ')
        
        text += f"{icon} <b>{info.get('service')}</b> - {status_text}\n"
        text += f"📞 <code>{info.get('phone')}</code>\n"
        text += f"📶 {info.get('network')}\n"
        
        if info.get('otp'):
            text += f"🔑 <code>{info.get('otp')}</code>\n"
        
        text += f"⏰ {info.get('created_at')}\n\n"
    
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text in ["❓ Hướng dẫn"])
def cmd_help(message):
    text = (
        "❓ <b>HƯỚNG DẪN SỬ DỤNG</b>\n\n"
        
        "<b>CÁCH DÙNG:</b>\n"
        "1️⃣ Chọn OKVIP1 hoặc OKVIP2\n"
        "2️⃣ Chọn nhà mạng\n"
        "3️⃣ Nhận số điện thoại\n"
        "4️⃣ Đợi mã OTP tự động\n\n"
        
        "<b>LỆNH:</b>\n"
        "/start - Khởi động bot\n\n"
        
        "<b>NHÀ MẠNG:</b>\n"
        "Mobifone, Vinaphone, Viettel\n"
        "Vietnamobile, ITelecom, Wintel"
    )
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['balance'])
def cmd_balance(message):
    result = get_balance()
    if result["status"] == 1:
        if ADMIN_ID and str(message.chat.id) == str(ADMIN_ID):
            bot.reply_to(message, f"💰 <b>Số dư:</b> ${result['balance']:,.2f}", parse_mode="HTML")
        else:
            bot.reply_to(message, "❌ Không có quyền xem số dư", parse_mode="HTML")
    else:
        bot.reply_to(message, f"❌ {result['message']}", parse_mode="HTML")

# ==================== CALLBACK HANDLERS ====================
@bot.callback_query_handler(func=lambda call: call.data.startswith('rent_'))
def callback_rent(call):
    parts = call.data.split('_')
    service_key = parts[1]
    network_code = parts[2]
    
    service = SERVICES[service_key]
    network_name = NETWORKS.get(network_code, network_code)
    
    bot.answer_callback_query(call.id, "Đang xử lý...")
    
    try:
        bot.edit_message_text(
            f"🎰 <b>OKVIP</b>\n\n"
            f"⏳ <b>Đang tìm số...</b>\n"
            f"📶 <b>Nhà mạng:</b> {network_name}",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML"
        )
    except:
        pass
    
    msg = call.message
    
    result = create_order(service['id'], network=network_code)
    
    if result["status"] == 1:
        req_id = result["id"]
        phone = result["phone"]
        
        user_orders[call.message.chat.id][req_id] = {
            'phone': phone,
            'service': 'OKVIP',
            'network': network_name,
            'status': 'waiting',
            'created_at': datetime.now().strftime('%H:%M:%S %d/%m')
        }
        
        text = (
            f"🎉 <b>THUÊ THÀNH CÔNG!</b>\n\n"
            f"🎰 <b>OKVIP</b>\n"
            f"📞 <b>Số điện thoại:</b>\n<code>{phone}</code>\n\n"
            f"📶 <b>Nhà mạng:</b> {network_name}\n"
            f"🆔 <b>Mã đơn:</b> <code>{req_id}</code>\n\n"
            f"⚡ <b>Đang chờ OTP tự động...</b>"
        )
        
        bot.edit_message_text(text, call.message.chat.id, msg.message_id, parse_mode="HTML")
        
        threading.Thread(
            target=auto_check_otp,
            args=(call.message.chat.id, req_id, phone, 'OKVIP', network_name),
            daemon=True
        ).start()
        
        logger.info(f"Order: {req_id} - {phone} - OKVIP - {network_name}")
    else:
        bot.edit_message_text(
            f"🎰 <b>OKVIP</b>\n\n"
            f"❌ <b>THUÊ SỐ THẤT BẠI</b>\n\n"
            f"<b>Lý do:</b> {result['message']}\n"
            f"📶 <b>Nhà mạng:</b> {network_name}\n\n"
            f"💡 Vui lòng thử lại sau",
            call.message.chat.id,
            msg.message_id,
            parse_mode="HTML"
        )

# ==================== FLASK ROUTES ====================
@app.route("/")
def home():
    html = f"""
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>OKVIP Bot</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            .container {{
                background: rgba(255, 255, 255, 0.95);
                border-radius: 20px;
                padding: 40px;
                max-width: 450px;
                width: 100%;
                box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
                text-align: center;
            }}
            h1 {{ 
                color: #667eea; 
                font-size: 2.5rem; 
                margin-bottom: 10px;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.1);
            }}
            .subtitle {{
                color: #64748b;
                font-size: 1.1rem;
                margin-bottom: 30px;
            }}
            .stats {{
                display: grid;
                grid-template-columns: repeat(2, 1fr);
                gap: 20px;
                margin: 30px 0;
            }}
            .stat {{
                background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
                padding: 25px;
                border-radius: 15px;
                transition: transform 0.3s, box-shadow 0.3s;
            }}
            .stat:hover {{
                transform: translateY(-5px);
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
            }}
            .stat-icon {{
                font-size: 2.5rem;
                margin-bottom: 10px;
            }}
            .stat-value {{ 
                font-size: 2rem; 
                font-weight: bold; 
                color: #1e293b;
                margin: 10px 0;
            }}
            .stat-label {{
                color: #64748b;
                font-size: 0.9rem;
            }}
            .footer {{ 
                margin-top: 30px; 
                color: #64748b; 
                font-size: 0.9rem;
                line-height: 1.6;
            }}
            .status-badge {{
                display: inline-block;
                background: #10b981;
                color: white;
                padding: 8px 20px;
                border-radius: 20px;
                font-weight: bold;
                margin-top: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎰 OKVIP BOT</h1>
            <p class="subtitle">Thuê số OTP tự động - Nhanh chóng & Tiện lợi</p>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-icon">⏳</div>
                    <div class="stat-value">{len(active_checks)}</div>
                    <div class="stat-label">Đang chờ OTP</div>
                </div>
                <div class="stat">
                    <div class="stat-icon">👥</div>
                    <div class="stat-value">{len(user_orders)}</div>
                    <div class="stat-label">Người dùng</div>
                </div>
            </div>
            
            <div class="status-badge">✅ ĐANG HOẠT ĐỘNG</div>
            
            <div class="footer">
                ⏰ {datetime.now().strftime('%H:%M:%S - %d/%m/%Y')}<br>
                📱 Hỗ trợ 6 nhà mạng Việt Nam<br>
                ⚡ Tự động nhận OTP trong 3-5 phút
            </div>
        </div>
    </body>
    </html>
    """
    return html, 200

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "active_checks": len(active_checks),
        "total_users": len(user_orders),
        "bot": "OKVIP Bot",
        "version": "1.0"
    }), 200

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    if USE_POLLING:
        return "OK", 200
    try:
        json_data = request.get_json()
        if json_data:
            update = telebot.types.Update.de_json(json_data)
            bot.process_new_updates([update])
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK", 200

# ==================== WEBHOOK SETUP ====================
def setup_webhook():
    if USE_POLLING:
        logger.info("🔄 Polling mode enabled")
        return
    try:
        if not WEBHOOK_URL:
            logger.error("❌ WEBHOOK_URL not set!")
            return
        time.sleep(2)
        bot.remove_webhook()
        time.sleep(1)
        webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{BOT_TOKEN}"
        result = bot.set_webhook(url=webhook_url, drop_pending_updates=True, max_connections=40)
        if result:
            logger.info(f"✅ Webhook set successfully: {webhook_url}")
        else:
            logger.error("❌ Failed to set webhook")
    except Exception as e:
        logger.error(f"❌ Webhook setup error: {e}")

def start_polling():
    logger.info("🔄 Starting polling mode...")
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(timeout=30, skip_pending=True)

# ==================== MAIN ====================
if __name__ == "__main__":
    try:
        logger.info("=" * 50)
        logger.info("🚀 Starting OKVIP Bot v1.0")
        logger.info("=" * 50)
        logger.info(f"📊 Mode: {'Polling' if USE_POLLING else 'Webhook'}")
        logger.info(f"🔐 Proxy: {'Enabled' if USE_PROXY else 'Disabled'}")
        
        if USE_POLLING:
            flask_thread = threading.Thread(
                target=lambda: app.run(
                    host="0.0.0.0", 
                    port=int(os.getenv("PORT", 10000)), 
                    debug=False, 
                    use_reloader=False
                ),
                daemon=True
            )
            flask_thread.start()
            logger.info("✅ Flask server started in background")
            time.sleep(2)
            start_polling()
        else:
            threading.Thread(target=setup_webhook, daemon=True).start()
            port = int(os.getenv("PORT", 10000))
            logger.info(f"🌐 Starting Flask server on port {port}")
            app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        raise
