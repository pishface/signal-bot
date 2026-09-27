import os
import re
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from metaapi_cloud_sdk import MetaApi

TELEGRAM_TOKEN = os.getenv("TOKEN")
METAAPI_TOKEN = os.getenv("API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")

async def get_connection():
    api = MetaApi(METAAPI_TOKEN)
    account = await api.metatrader_account_api.get_account(ACCOUNT_ID)

    if account.state != 'DEPLOYED':
        await account.deploy()
        await account.wait_connected()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()
    return connection

async def place_trade(symbol, direction, order_type, entry, sl, tps, volume=0.01):
    connection = await get_connection()
    results = []
    volume_per_tp = max(0.01, round(volume / len(tps), 2))

    try:
        for i, tp in enumerate(tps):
            current_volume = volume_per_tp if i < len(tps) - 1 else round(volume - volume_per_tp * (len(tps) - 1), 2)
            options = {"stop_loss": float(sl), "take_profit": float(tp)}

            if order_type == "MARKET":
                if direction == "BUY":
                    await connection.create_market_buy_order(symbol, current_volume, **options)
                else:
                    await connection.create_market_sell_order(symbol, current_volume, **options)
            elif order_type == "LIMIT":
                if direction == "BUY":
                    await connection.create_limit_buy_order(symbol, current_volume, float(entry), **options)
                else:
                    await connection.create_limit_sell_order(symbol, current_volume, float(entry), **options)
            elif order_type == "STOP":
                if direction == "BUY":
                    await connection.create_stop_buy_order(symbol, current_volume, float(entry), **options)
                else:
                    await connection.create_stop_sell_order(symbol, current_volume, float(entry), **options)

            results.append(f"TP{i+1}: {tp}")

        return f"✅ Order(s) placed!\n\n{direction} {order_type} {symbol}\nEntry: {entry or 'Market'}\nSL: {sl}\n" + "\n".join(results)
    except Exception as e:
        return f"❌ Error: {str(e)}"
    finally:
        await connection.close()

async def close_positions(symbol=None):
    connection = await get_connection()
    try:
        positions = await connection.get_positions()
        if not positions:
            return "No open positions found."

        closed = []
        for pos in positions:
            if symbol is None or pos["symbol"].upper() == symbol.upper():
                await connection.close_position(pos["id"])
                closed.append(f"{pos['symbol']} {pos['type']} {pos['volume']}")

        if closed:
            return "✅ Closed:\n" + "\n".join(closed)
        else:
            return f"No open positions found for {symbol}."
    except Exception as e:
        return f"❌ Error closing: {str(e)}"
    finally:
        await connection.close()

async def show_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    connection = await get_connection()
    try:
        positions = await connection.get_positions()
        if not positions:
            await update.message.reply_text("No open positions.")
            return

        lines = []
        for pos in positions:
            profit = pos.get("unrealizedProfit", 0)
            lines.append(
                f"{pos['symbol']} | {pos['type']} | {pos['volume']} lots\n"
                f"Entry: {pos['openPrice']} | SL: {pos.get('stopLoss')} | TP: {pos.get('takeProfit')}\n"
                f"Profit: {profit:.2f}"
            )
        await update.message.reply_text("Open positions:\n\n" + "\n\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    finally:
        await connection.close()

def parse_signal(text):
    text = text.upper().replace(",", ".")
    text = text.replace("GOLD", "XAUUSD")

    # Close command detection
    if re.search(r'CLOSE.*PIPS?|CLOSE\s+IN', text):
        symbol = None
        for s in ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]:
            if s in text:
                symbol = s
                break
        return "CLOSE", symbol, None, None, None, None

    direction = None
    if re.search(r'\bBUY\b', text):
        direction = "BUY"
    elif re.search(r'\bSELL\b', text):
        direction = "SELL"

    order_type = "MARKET"
    if "LIMIT" in text:
        order_type = "LIMIT"
    elif "STOP" in text:
        order_type = "STOP"

    symbols = ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD", "BTCUSD", "ETHUSD"]
    symbol = None
    for s in symbols:
        if s in text:
            symbol = s
            break

    entry = None
    entry_match = re.search(r'(?:AT|@|ENTRY|PRICE)[:\s]*([\d.]+)', text)
    if entry_match:
        entry = entry_match.group(1)
    else:
        price_match = re.search(r'(?:BUY|SELL)\s+(?:LIMIT|STOP)\s+(?:AT\s+)?([\d.]+)', text)
        if price_match:
            entry = price_match.group(1)

    sl = None
    sl_match = re.search(r'(?:SL|STOP\s*LOSS|S/L)[:\s]*([\d.]+)', text)
    if sl_match:
        sl = sl_match.group(1)

    tps = re.findall(r'(?:TP\d*|TAKE\s*PROFIT|T/P)[:\s]*([\d.]+)', text)

    if direction and symbol and sl and tps:
        if order_type != "MARKET" and not entry:
            return None
        return symbol, direction, order_type, entry, sl, tps
    return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    parsed = parse_signal(text)

    if not parsed:
        await update.message.reply_text(
            "❌ Could not understand the signal.\n\n"
            "Need: BUY/SELL + Symbol (or GOLD) + SL + at least one TP\n"
            "Or: close in X pips"
        )
        return

    if parsed[0] == "CLOSE":
        _, symbol, *_ = parsed
        await update.message.reply_text(f"Closing positions{' for ' + symbol if symbol else ''}...")
        result = await close_positions(symbol)
        await update.message.reply_text(result)
        return

    symbol, direction, order_type, entry, sl, tps = parsed

    await update.message.reply_text(
        f"Processing {direction} {order_type} {symbol}...\n"
        f"Entry: {entry or 'Market'} | SL: {sl} | TPs: {', '.join(tps)}"
    )

    result = await place_trade(symbol, direction, order_type, entry, sl, tps)
    await update.message.reply_text(result)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("positions", show_positions))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
