# Project: Diabetes Assistant Telegram Bot

## Goal

Create a Telegram bot assistant for a person with type 1 diabetes.

Initial version:
- user asks about food in Russian
- bot finds carbohydrate content
- bot calculates carbohydrates for a given amount
- bot stores verified food data locally
- next time bot uses local database instead of internet lookup

Later versions:
- meal diary
- insulin diary
- activity tracking
- personal insulin-to-carb ratio model

## Tech stack

Use:

- Python 3.12+
- aiogram 3
- PostgreSQL
- SQLAlchemy 2 async
- Alembic migrations
- Pydantic v2
- PydanticAI
- OpenAI API
- Docker Compose

Do NOT use:
- LangChain
- CrewAI
- vector database
- RAG
- multi-agent architecture

Keep architecture simple.

## Architecture

Telegram
    |
 aiogram bot
    |
 PydanticAI agent
    |
 tools:
    - search local food database
    - lookup food online if missing
    - save new food data
    - calculate carbs
    - add a personal journal entry
    |
 PostgreSQL


## Main user scenarios

Example:

User:

"Сколько углеводов в 150 г вареной гречки?"

Expected flow:

1. Parse:
- food: гречка вареная
- amount: 150
- unit: grams

2. Search PostgreSQL.

3. If found:
calculate:
carbs = amount / 100 * carbs_per_100g

Answer:
"В 150 г вареной гречки примерно 30 г углеводов"

4. If not found:
- search online using OpenAI/web tool
- normalize result
- save to database
- answer user

User-provided value:

User:

"В моём хлебе 42 г углеводов на 100 г"

Expected flow:

1. Parse:
- food: мой хлеб
- carbohydrates: 42
- amount: 100 grams

2. Normalize carbohydrates to 100 grams.

3. Create or update the food in PostgreSQL with `source = user_provided`.

4. Confirm the saved value. Future calculations must use this local value.

Database access in Telegram:

- `/foods` lists all saved foods and carbohydrate values in the chat.
- `/export_csv` sends the complete food database as a UTF-8 CSV document.

Access control:

- Only active users stored in PostgreSQL may use the bot.
- Unauthorized users receive their Telegram ID to send to an administrator.
- Bootstrap administrators are configured through `TELEGRAM_ADMIN_IDS` and saved to PostgreSQL.
- Administrators add users with `/add_user <telegram_id> [name]`.
- Administrators view the whitelist with `/users`.

Personal journal:

- Every journal entry belongs to exactly one Telegram user.
- An entry has a timestamp and may include duration, short insulin, long insulin,
  food, carbohydrate grams, physical activity, and blood glucose in mmol/L.
- Users may enter carbohydrates in grams or ХЕ. The database stores grams only;
  `JOURNAL_XE_CARBS_GRAMS` configures the conversion and defaults to 12 g per ХЕ.
- Users add entries with `/log <data>` or a natural-language request such as
  "Запиши сахар 6.4 ммоль/л, короткий инсулин 3 ед., прогулка 30 минут".
- `/journal [limit]` shows only the current user's recent entries.
- `/export_journal_csv` sends the current user's complete journal as a UTF-8 CSV.
- `/import [year]` imports Hematonix `.xls/.xlsx` glucose readings and MelStudio
  `.txt` diary rows into the current user's journal.
- MelStudio dates without a year use the command year or the current year.
- Re-importing identical entries does not create duplicates.
- The bot stores insulin values but never calculates or recommends insulin doses.


## Database

Create table foods:

fields:

id
canonical_name
ru_name
en_name

carbs_per_100g
protein_per_100g
fat_per_100g
kcal_per_100g
glycemic_index

source
confidence

created_at
updated_at


Create table food_aliases:

id
food_id
alias

Create table telegram_users:

fields:

telegram_user_id
username
full_name
is_admin
is_active
added_by_telegram_id
created_at
updated_at
last_seen_at

Create table journal_entries:

fields:

id
telegram_user_id
occurred_at
duration_minutes
short_insulin_units
long_insulin_units
food
carbohydrates_grams
physical_activity
blood_glucose_mmol_l
created_at


Example:

foods:
canonical_name = buckwheat_cooked
ru_name = гречка вареная
carbs_per_100g = 19.9

aliases:
гречка
греча
гречневая каша


## Agent rules

LLM should NOT directly access database.

Use tools:

find_food(name)

lookup_food_online(name)

save_food(food)

save_user_food(name, carbs_grams, amount_grams)

calculate_carbs(food, amount)


Agent must prefer:
1. Local database
2. Online lookup only if missing

Online lookup stores carbohydrates, protein, fat, kcal per 100 grams, and a
measured glycemic index when reliable data exists for the exact preparation.
It must not infer glycemic index from nutrients, glycemic load, or similar foods.


## Safety rules

This is diabetes-related software.

For now:
- calculate carbohydrates
- store user-reported journal data
- do NOT recommend insulin doses
- do NOT give medical advice


## Project structure

Suggested:

app/

bot/
  handlers.py

agent/
  food_agent.py
  tools.py

database/
  models.py
  session.py
  repositories.py

services/
  carbs.py

config.py

main.py


## Stage 1 task

Implement:

- project structure
- Docker Compose with PostgreSQL
- database models
- migrations
- Telegram bot
- food lookup agent
- local food cache
- carbohydrate calculation

Create clean typed async Python code.
