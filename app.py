from flask import Flask, request, jsonify, render_template
from groq import Groq
import mysql.connector
import os
import re

app = Flask(__name__)

# ─────────────────────────────────────────
# CONFIG — loaded from environment variables
# Never hardcode passwords or API keys!
# ─────────────────────────────────────────

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST",     "localhost"),
    "user":     os.environ.get("DB_USER",     "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME",     "sql_assistant")
}

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY)


# ─────────────────────────────────────────
# DATABASE HELPERS
# ─────────────────────────────────────────

def get_connection():
    return mysql.connector.connect(**DB_CONFIG)


def get_schema():
    return """
    MySQL Database: sql_assistant

    TABLE customers:
      customer_id INT PRIMARY KEY
      name        VARCHAR
      email       VARCHAR
      city        VARCHAR

    TABLE products:
      product_id  INT PRIMARY KEY
      name        VARCHAR
      category    VARCHAR
      price       DECIMAL

    TABLE orders:
      order_id    INT PRIMARY KEY
      customer_id INT  → customers.customer_id
      product_id  INT  → products.product_id
      quantity    INT
      order_date  DATE

    SAMPLE DATA:
      customers: Akash Kumar (Chennai), Priya Sharma (Mumbai), Ravi Patel (Delhi),
                 Sneha Menon (Bangalore), Arjun Nair (Hyderabad)
      products:  Laptop (Electronics,55000), Phone (Electronics,20000),
                 Headphones (Electronics,3000), Desk Chair (Furniture,12000),
                 Notebook (Stationery,200)
      orders:    10 orders linking customers to products with quantities
    """


def generate_sql(user_question):
    schema = get_schema()
    prompt = f"""
You are an expert MySQL developer. Convert natural language questions to MySQL queries.

DATABASE SCHEMA:
{schema}

USER QUESTION: "{user_question}"

INSTRUCTIONS:
1. Understand what the user wants — even if phrased casually or with typos
2. Write the correct MySQL query
3. Use JOINs whenever data from multiple tables is needed
4. For aggregations use GROUP BY properly
5. For rankings use ORDER BY with LIMIT
6. Support all operations: SELECT, INSERT, UPDATE, DELETE
7. For INSERT always provide all required columns
8. For UPDATE and DELETE always use WHERE clause
9. Handle these question types:
   - "show customers with products they ordered" → JOIN customers+orders+products
   - "top customers by spending" → SUM(price*quantity) GROUP BY customer
   - "how many orders per customer" → COUNT with GROUP BY
   - "total revenue" → SUM(price*quantity)
   - "products never ordered" → LEFT JOIN with IS NULL
   - "customers who bought electronics" → JOIN with WHERE category
   - "most popular product" → COUNT orders GROUP BY product ORDER BY count DESC
   - "orders this month / by date" → WHERE order_date conditions
   - "add / insert new record" → INSERT INTO
   - "update / change a record" → UPDATE with WHERE
   - "delete / remove a record" → DELETE with WHERE

REPLY IN EXACTLY THIS FORMAT (no extra text):
SQL: <complete mysql query on one line>
EXPLANATION: <plain English explanation of what this query does>
"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )

    raw = response.choices[0].message.content.strip()
    sql, explanation = "", ""

    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("SQL:"):
            sql = line.replace("SQL:", "").strip()
        elif line.startswith("EXPLANATION:"):
            explanation = line.replace("EXPLANATION:", "").strip()

    if not sql:
        match = re.search(
            r'(SELECT|INSERT|UPDATE|DELETE)[\s\S]+?(?=EXPLANATION:|$)',
            raw, re.IGNORECASE
        )
        if match:
            sql = match.group(0).strip()

    return {"sql": sql, "explanation": explanation, "raw": raw}


def run_query(sql):
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        sql_type = sql.strip().upper().split()[0]

        if sql_type == "SELECT":
            columns = [desc[0] for desc in cursor.description]
            rows    = cursor.fetchall()
            conn.close()
            return {
                "columns": columns,
                "rows":    [list(r) for r in rows],
                "error":   None,
                "type":    "SELECT",
                "count":   len(rows)
            }
        else:
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return {
                "columns":       [],
                "rows":          [],
                "affected_rows": affected,
                "error":         None,
                "type":          sql_type
            }
    except Exception as e:
        return {"columns": [], "rows": [], "error": str(e), "type": "ERROR"}


# ─────────────────────────────────────────
# FLASK ROUTES
# ─────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/query", methods=["POST"])
def query():
    data          = request.json
    user_question = data.get("question", "").strip()
    if not user_question:
        return jsonify({"error": "Empty question"})

    ai_result = generate_sql(user_question)
    db_result = run_query(ai_result["sql"])

    return jsonify({
        "sql":         ai_result["sql"],
        "explanation": ai_result["explanation"],
        "result":      db_result
    })


@app.route("/stats")
def stats():
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM customers")
        customers = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM products")
        products = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM orders")
        orders = cursor.fetchone()[0]
        cursor.execute("""
            SELECT COALESCE(SUM(p.price * o.quantity), 0)
            FROM orders o
            JOIN products p ON o.product_id = p.product_id
        """)
        revenue = int(cursor.fetchone()[0])
        conn.close()
        return jsonify({
            "customers": customers,
            "products":  products,
            "orders":    orders,
            "revenue":   revenue
        })
    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    app.run(debug=True)
