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

source
confidence

created_at
updated_at


Create table food_aliases:

id
food_id
alias


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


## Safety rules

This is diabetes-related software.

For now:
- only calculate carbohydrates
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
