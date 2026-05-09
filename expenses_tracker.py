from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import re
import asyncio

# from config import BOT_TOKEN
import json

import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

# TOKEN = BOT_TOKEN

scope = [
'https://spreadsheets.google.com/feeds',
'https://www.googleapis.com/auth/drive'
]


google_creds_json = os.getenv("GOOGLE_CREDENTIALS")

if google_creds_json:
    creds_dict = json.loads(google_creds_json)

    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict,
        scope
    )
else:
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        'credentials.json',
        scope
    )

client = gspread.authorize(creds)

data_sheet = client.open("Expenses tracker").worksheet("data")

def parse_transaction(text):

    text = text.lower()

    amount = None

    # Handle XmY format (3m5)
    match = re.search(r'(\d+)m(\d+)', text)

    if match:

        millions = int(match.group(1))
        thousands = int(match.group(2))

        if thousands < 10:
            thousands *= 100000
        elif thousands < 100:
            thousands *= 10000
        else:
            thousands *= 1000

        amount = millions * 1000000 + thousands

        text = text.replace(match.group(0), "")

    # Handle decimal m
    elif re.search(r'(\d+(?:\.\d+)?)m', text):

        match = re.search(r'(\d+(?:\.\d+)?)m', text)

        amount = int(float(match.group(1)) * 1000000)

        text = text.replace(match.group(0), "")

    # Handle k
    elif re.search(r'(\d+(?:\.\d+)?)k', text):

        match = re.search(r'(\d+(?:\.\d+)?)k', text)

        amount = int(float(match.group(1)) * 1000)

        text = text.replace(match.group(0), "")

    elif re.search(r'\d+', text):

        match = re.search(r'\d+', text)

        amount = int(match.group(0))

        text = text.replace(match.group(0), "")

    # Clean remaining text
    category = text.strip().title()

    return amount, category

def find_month_row(sheet, month_str):
    data = sheet.get_all_values()

    for i, row in enumerate(data[1:], start=2):  # skip header
        if row[0] == month_str:
            budget = int(row[2]) if row[2] else 0
            spending = int(row[3]) if row[3] else 0
            return i, budget, spending

    return None, 0, 0

def get_month_str(date):
    return f"{date.year}-{date.month:02d}"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text
        amount, merchant = parse_transaction(text)

        if amount is None:
            await update.message.reply_text("Couldn't parse amount 😅")
            return

        message_time = update.message.date + timedelta(hours=7)
        month_str = get_month_str(message_time)

        # Save transaction
        await asyncio.to_thread(data_sheet.append_row, [
            str(message_time.strftime("%Y-%m-%d %H:%M:%S")),
            amount,
            merchant,
            "",
            text
        ])

        # Access Monthly Budget sheet
        budget_sheet = client.open("Expenses tracker").worksheet("Monthly Budget")

        # Read current values
        row_index, budget, spending = await asyncio.to_thread(
            find_month_row,
            budget_sheet,
            month_str
        )

        if row_index is None:
            await update.message.reply_text(
                f"No budget set for {month_str} ❗"
            )
            return

        # ✅ Core logic (your idea)
        new_spending = spending + amount

        # Update only 1 cell (column C)
        await asyncio.to_thread(
            budget_sheet.update_cell,
            row_index,
            4,
            new_spending
        )

        remaining = budget - new_spending

        await update.message.reply_text(
            f"Saved {amount:,} ({merchant})\n"
            f"Remaining Budget: {remaining:,}"
        )

    except Exception as e:
        print(e)
        await update.message.reply_text("Something went wrong 😢")

# async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):

#     text = update.message.text
#     amount, category = parse_transaction(text)

#     data_sheet.append_row([
#         update.message.date+ timedelta(hours=7),
#         amount,
#         "",
#         category,
#         text
#     ])

#     await update.message.reply_text(
#         f"Saved {amount}, {category}"
#     )


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(
MessageHandler(filters.TEXT, handle)
)

app.run_polling()