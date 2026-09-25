import os
import re
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from metaapi_cloud_sdk import MetaApi

TELEGRAM_TOKEN = os.getenv("TOKEN")
METAAPI_TOKEN = os.getenv("API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")

async def place_trade(symbol, direction, sl, tp, volume=0.01):
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != 'DEPLOYED':
        await account.deploy()
        await account.wait_connected()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    try:
        if direction.upper() == "BUY":
            result = await connection.create_market_buy_order(
                symbol, volume, stop_loss=float(sl), take_profit=float(tp)
            )
        else:
            result = await connection.create_market_sell_order(
                symbol, volume, stop_loss=float(sl), take_profit=float(tp)
            )
        return f"✅ Trade placed\nSymbol: {symbol}\nDirection: {direction}\nSL: {sl}\nTP: {tp}"
    except Exception as e:
        return f"❌ Error placing trade: {str(e)}"
    finally:
        await connection.close()

def parse_signal(text):
    text = text.upper().replace(",", ".")
    
    # Common patterns
    direction = None
    symbol = None
    sl = None
    tp = None

    # Find direction
    if "BUY" in text:
        direction = "BUY"
    elif "SELL" in text:
        direction = "SELL"

    # Find symbol (common ones)
    symbols = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "BTCUSD", "ETHUSD"]
    for s in symbols:
        if s in text:
            symbol = s
            break

    # Find SL
    sl_match = re.search(r'SL[:\s]*([\d.]+)', text)
    if sl_match:
        sl = sl_match.group(1)

    # Find TP
    tp_match = re.search(r'TP[:\s]*([\d.]+)', text)
    if tp_match:
        tp = tp_match.group(1)

    if direction and symbol and sl and tp:
        return symbol, direction, sl, tp
    return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    parsed = parse_signal(text)

    if not parsed:
        await update.message.reply_text(
            "Could not understand the signal.\n\n"
            "Please make sure it contains:\n"
            "- BUY or SELL\n"
            "- Symbol (e.g. XAUUSD)\n"
            "- SL and TP"
        )
        return

    symbol, direction, sl, tp = parsed

    await update.message.reply_text(f"Processing {direction} {symbol}...\nSL: {sl} | TP: {tp}")

    result = await place_trade(symbol, direction, sl, tp)
    await update.message.reply_text(result)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
