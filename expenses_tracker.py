from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes, CommandHandler

from collections import defaultdict

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta, timezone
import re
import asyncio
import google.generativeai as genai

import json
import os
from dotenv import load_dotenv

load_dotenv()

##   SETUP TOKEN & KEYS

#   SETUP TOKEN FOR TELEGRAM BOT
TOKEN = os.getenv("BOT_TOKEN")

scope = [
'https://spreadsheets.google.com/feeds',
'https://www.googleapis.com/auth/drive'
]

#   SETUP CREDENTIALS FOR GOOGLE DRIVE
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

#   SETUP API KEY FOR GEMINI
genai.configure(
    api_key=os.getenv("GEMINI_API_KEY")
)

model = genai.GenerativeModel(
    "gemini-2.5-flash"
)


#   GET GOOGLES SHEET DATA
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

def detect_category_from_sheet(sheet, merchant):

    rows = sheet.get_all_values()[1:]

    merchant = merchant.lower().strip()

    for row in reversed(rows):

        if len(row) < 4:
            continue

        existing_merchant = row[2].lower().strip()
        existing_category = row[3].strip()

        if existing_merchant == merchant:

            return existing_category

    return "Other"

async def classify_transaction(text):

    prompt = f"""
    Categorize this finance transaction.

    Transaction:
    {text}

    Return ONLY one category from:

    Transport
    Xăng
    Drink
    Food
    Medicine
    Grab
    Other
    Unknown
    Gủi xe
    Kem
    Bike Maintenance
    Extra Income
    Badminton
    Traveling

    """

    response = await asyncio.to_thread(
        model.generate_content,
        prompt
    )

    return response.text.strip()


# Generate spending report
# Example:
# /report
# /report 2026-05
# /report 2026-01 2026-05
def generate_report(sheet, start_month, end_month=None):

    # Read all rows except header
    rows = sheet.get_all_values()[1:]

    # Auto create dictionary with default value = 0
    report = defaultdict(int)

    # If user only provides 1 month:
    # /report 2026-05
    #
    # then end_month = start_month
    if end_month is None:
        end_month = start_month

    # Loop through every transaction row
    for row in rows:

        # Skip broken/incomplete rows
        if len(row) < 4:
            continue

        # Sheet format:
        # Date | Amount | Merchant | Category | Raw Text
        date_str = row[0]
        amount = row[1]
        category = row[3]

        # Skip if amount empty
        if not amount:
            continue

        # Extract YYYY-MM
        #
        # Example:
        # 2026-05-09 10:20:30
        # becomes:
        # 2026-05
        month = date_str[:7]

        # Check if transaction month is inside range
        #
        # Example:
        # start = 2026-01
        # end = 2026-05
        #
        # then include:
        # 2026-03
        if start_month <= month <= end_month:

            # Add amount into category total
            report[category] += int(amount)

    return report


# Telegram command handler
#
# Handles:
# /report
# /report 2026-05
# /report 2026-01 2026-05
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args

    # CASE 1:
    # /report
    # Use current month
    # =========================
    if len(args) == 0:

        now = datetime.now()

        start_month = f"{now.year}-{now.month:02d}"
        end_month = start_month

    # =========================
    # CASE 2:
    # /report 2026-05
    #
    # Single month report
    # =========================
    elif len(args) == 1:

        start_month = args[0]
        end_month = start_month

    # =========================
    # CASE 3:
    # /report 2026-01 2026-05
    #
    # Month range report
    # =========================
    else:

        start_month = args[0]
        end_month = args[1]

    report_data = await asyncio.to_thread(
        generate_report,
        data_sheet,
        start_month,
        end_month
    )

    # No spending found
    if not report_data:

        await update.message.reply_text(
            "No spending found"
        )

        return

    # Build message lines
    lines = []

    total = 0

    # Sort categories by highest spending first
    for category, amount in sorted(
        report_data.items(),
        key=lambda x: x[1],
        reverse=True
    ):

        # Add category line
        lines.append(
            f"{category}: {amount:,}"
        )

        total += amount

    # Access Monthly Budget sheet to get month budget
    budget_sheet = client.open("Expenses tracker").worksheet("Monthly Budget")
    month_str = get_month_str(datetime.now(timezone(timedelta(hours=7))))
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


    remaining = budget - total
    # Add total spending
    lines.append(
        f"\nTotal: {total:,}\n Remaining: {remaining:,}"
    )

    # Send report back to Telegram
    await update.message.reply_text(
        "\n".join(lines)
    )

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text
        amount, merchant = parse_transaction(text)
        category = detect_category_from_sheet(
            data_sheet,
            merchant
        )

        if category == "Other":
            category = await classify_transaction(text)

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
            category,
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
        if category == 'Unknown':
            await update.message.reply_text(
                f"Cannot classify category"
            )

        await update.message.reply_text(
            f"Saved {amount:,} ({merchant})\n"
            f"Remaining Budget: {remaining:,}"
        )

    except Exception as e:
        print(e)
        await update.message.reply_text("Something went wrong 😢")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(
    CommandHandler("report", report)
)
app.add_handler(
MessageHandler(filters.TEXT, handle)
)

app.run_polling()