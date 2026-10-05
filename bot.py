import os
import re
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from metaapi_cloud_sdk import MetaApi

TELEGRAM_TOKEN = os.getenv("TOKEN")
METAAPI_TOKEN = os.getenv("API_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")

# Temporary storage for pending trades that need adjustment
pending_trades = {}

# Minimum distance in points for gold (you can change this)
MIN_DISTANCE_GOLD = 40

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
            options = {
                "stop_loss": float(sl),
                "take_profit": float(tp)
            }

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

        return True, f"✅ Order(s) placed!\n\n{direction} {order_type} {symbol}\nEntry: {entry or 'Market'}\nSL: {sl}\n" + "\n".join(results)

    except Exception as e:
        error_msg = str(e).lower()
        if "invalid stops" in error_msg or "validation failed" in error_msg:
            return False, "TOO_CLOSE"
        elif "requote" in error_msg:
            return False, "❌ Requote – price moved too fast. Try again in a few seconds."
        elif "market is closed" in error_msg:
            return False, "❌ Market is currently closed for this symbol."
        else:
            return False, f"❌ Error: {str(e)}"
    finally:
        await connection.close()

def adjust_levels(direction, entry, sl, tps, min_dist=MIN_DISTANCE_GOLD):
    """Automatically push SL and TPs to a safer distance"""
    entry = float(entry) if entry else None
    sl = float(sl)
    tps = [float(tp) for tp in tps]

    if direction == "BUY":
        # SL must be below entry
        if entry:
            new_sl = entry - min_dist
        else:
            new_sl = sl - min_dist if sl > 0 else sl
        new_tps = [max(tp, (entry or tp) + min_dist) for tp in tps]
    else:
        # SELL
        if entry:
            new_sl = entry + min_dist
        else:
            new_sl = sl + min_dist
        new_tps = [min(tp, (entry or tp) - min_dist) for tp in tps]

    return str(round(new_sl, 2)), [str(round(tp, 2)) for tp in new_tps]

async def close_positions(symbol=None, position_id=None):
    connection = await get_connection()
    try:
        positions = await connection.get_positions()
        if not positions:
            return "No open positions found."

        closed = []
        for pos in positions:
            match_symbol = symbol is None or pos["symbol"].upper() == symbol.upper()
            match_id = position_id is None or str(pos["id"]) == str(position_id)
            if match_symbol and match_id:
                await connection.close_position(pos["id"])
                closed.append(f"{pos['symbol']} {pos['type']} {pos['volume']} (ID: {pos['id']})")

        if closed:
            return "✅ Closed:\n" + "\n".join(closed)
        return "No matching open positions found."
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
                f"ID: {pos['id']}\n"
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

    if re.search(r'\bCLOSE\b', text):
        symbol = None
        position_id = None
        for s in ["XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD"]:
            if s in text:
                symbol = s
                break
        id_match = re.search(r'\b(\d{5,})\b', text)
        if id_match:
            position_id = id_match.group(1)
        return "CLOSE", symbol, position_id, None, None, None

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
    range_match = re.search(r'([\d.]+)\s*[-–/]{1,2}\s*([\d.]+)', text)
    if range_match:
        low = float(range_match.group(1))
        high = float(range_match.group(2))
        if direction == "BUY":
            entry = str(min(low, high))
            order_type = "LIMIT"
        elif direction == "SELL":
            entry = str(max(low, high))
            order_type = "LIMIT"
    else:
        entry_match = re.search(r'(?:ENTRY\s*POINT|ENTRY|AT|@|PRICE|NOW)[:\s]*([\d.]+)', text)
        if entry_match:
            entry = entry_match.group(1)
        else:
            price_match = re.search(r'(?:BUY|SELL)\s+(?:LIMIT|STOP|NOW)?\s*(?:AT\s*)?([\d.]+)', text)
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
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # Check if user is replying to a pending trade decision
    if chat_id in pending_trades:
        choice = text.strip()
        trade = pending_trades.pop(chat_id)

        if choice.lower() in ["n", "no"]:
            await update.message.reply_text("❌ Trade cancelled.")
            return
        elif choice.lower() in ["y", "yes"]:
            # Auto adjust
            new_sl, new_tps = adjust_levels(
                trade["direction"],
                trade["entry"],
                trade["sl"],
                trade["tps"]
            )
            await update.message.reply_text(
                f"Adjusting to safer levels...\n"
                f"New SL: {new_sl}\n"
                f"New TPs: {', '.join(new_tps)}"
            )
            success, result = await place_trade(
                trade["symbol"], trade["direction"], trade["order_type"],
                trade["entry"], new_sl, new_tps
            )
            await update.message.reply_text(result if success else result)
            return
        else:
            await update.message.reply_text("Please reply with **yes** or **no**.")
            pending_trades[chat_id] = trade
            return

    # Normal signal parsing
    parsed = parse_signal(text)

    if not parsed:
        await update.message.reply_text(
            "❌ Could not understand the signal.\n\n"
            "Need: BUY/SELL + Symbol (or GOLD) + SL + at least one TP"
        )
        return

    if parsed[0] == "CLOSE":
        _, symbol, position_id, *_ = parsed
        msg = "Closing"
        if symbol:
            msg += f" {symbol}"
        if position_id:
            msg += f" ID {position_id}"
        await update.message.reply_text(msg + "...")
        result = await close_positions(symbol, position_id)
        await update.message.reply_text(result)
        return

    symbol, direction, order_type, entry, sl, tps = parsed

    await update.message.reply_text(
        f"Processing {direction} {order_type} {symbol}...\n"
        f"Entry: {entry or 'Market'} | SL: {sl} | TPs: {', '.join(tps)}"
    )

    success, result = await place_trade(symbol, direction, order_type, entry, sl, tps)

    if result == "TOO_CLOSE":
        # Save the trade and ask the user
        pending_trades[chat_id] = {
            "symbol": symbol,
            "direction": direction,
            "order_type": order_type,
            "entry": entry,
            "sl": sl,
            "tps": tps
        }
        await update.message.reply_text(
            "❌ Stops / TPs are too close for this broker.\n\n"
            "What do you want to do?\n"
            "1️⃣ Cancel the trade\n"
            "2️⃣ Automatically adjust to safe minimum distance\n\n"
            "Reply with **1** or **2**"
        )
    else:
        await update.message.reply_text(result)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("positions", show_positions))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
