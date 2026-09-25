import os
import re
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from metaapi_cloud_sdk import MetaApi

# Environment variables
TELEGRAM_TOKEN = os.getenv("TOKEN")
METAAPI_TOKEN = os.getenv("API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
RISK_PERCENT = float(os.getenv("RISK_FACTOR", "0.01"))

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
            result = await connection.create_market_buy_order(symbol, volume, stop_loss=sl, take_profit=tp)
        else:
            result = await connection.create_market_sell_order(symbol, volume, stop_loss=sl, take_profit=tp)
        return f"✅ Trade placed\nOrder ID: {result.get('orderId', 'N/A')}"
    except Exception as e:
        return f"❌ Error placing trade: {str(e)}"
    finally:
        await connection.close()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.upper()
    
    # Simple parser: looking for BUY/SELL + SYMBOL + SL + TP
    # Example format: BUY XAUUSD SL 2650 TP 2670
    match = re.search(r'(BUY|SELL)\s+([A-Z0-9]+).*?SL\s*([\d.]+).*?TP\s*([\d.]+)', text, re.IGNORECASE)
    
    if not match:
        await update.message.reply_text(
            "Send in this format:\n\nBUY XAUUSD SL 2650 TP 2670\n\nor\n\nSELL EURUSD SL 1.0850 TP 1.0750"
        )
        return

    direction, symbol, sl, tp = match.groups()
    sl = float(sl)
    tp = float(tp)

    await update.message.reply_text(f"Processing {direction} {symbol}...")

    result = await place_trade(symbol, direction, sl, tp)
    await update.message.reply_text(result)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
