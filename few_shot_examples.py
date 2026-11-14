# Few-shot examples for the prompt. Written by hand, NOT taken from
# test_questions.json or heldout_questions.json.
# Day 24: example 3 replaced - the old one (top seller by revenue in SP/RJ/MG)
# was the same question as held-out Q118 (caught by the result check in
# check_examples.py). Old version kept in few_shot_examples_day12.py.


EXAMPLES = [
    {
        "question": "How many unique customers from the state RJ placed an order in 2018?",
        "sql": """SELECT COUNT(DISTINCT c.customer_unique_id) AS unique_customers
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
WHERE c.customer_state = 'RJ'
  AND o.order_purchase_timestamp >= '2018-01-01'
  AND o.order_purchase_timestamp < '2019-01-01';""",
    },
    {
        "question": "Which 5 product categories had the lowest total revenue for orders purchased in the first half of 2018? Show the revenue.",
        "sql": """SELECT COALESCE(t.product_category_name_english, p.product_category_name) AS category,
       ROUND(SUM(oi.price)::numeric, 2) AS revenue
FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
JOIN products p ON p.product_id = oi.product_id
LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
WHERE o.order_purchase_timestamp >= '2018-01-01'
  AND o.order_purchase_timestamp < '2018-07-01'
GROUP BY 1
ORDER BY revenue ASC
LIMIT 5;""",
    },
    {
        "question": "For each of the categories bed_bath_table, health_beauty and sports_leisure, which seller sold the most items? Show the number of items.",
        "sql": """WITH seller_items AS (
    SELECT t.product_category_name_english AS category, oi.seller_id, COUNT(*) AS items_sold
    FROM order_items oi
    JOIN products p ON p.product_id = oi.product_id
    LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
    WHERE t.product_category_name_english IN ('bed_bath_table', 'health_beauty', 'sports_leisure')
    GROUP BY t.product_category_name_english, oi.seller_id
),
ranked AS (
    SELECT category, seller_id, items_sold,
           ROW_NUMBER() OVER (PARTITION BY category ORDER BY items_sold DESC) AS rn
    FROM seller_items
)
SELECT category, seller_id, items_sold
FROM ranked
WHERE rn = 1
ORDER BY category;""",
    },
    {
        "question": "How many sellers in the state SP sold items in more than 100 different orders?",
        "sql": """SELECT COUNT(*) AS sellers
FROM (
    SELECT s.seller_id
    FROM sellers s
    JOIN order_items oi ON oi.seller_id = s.seller_id
    WHERE s.seller_state = 'SP'
    GROUP BY s.seller_id
    HAVING COUNT(DISTINCT oi.order_id) > 100
) big_sellers;""",
    },
]