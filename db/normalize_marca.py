import psycopg2

conn = psycopg2.connect(
    host='aws-0-us-west-2.pooler.supabase.com',
    port=6543,
    dbname='postgres',
    user='postgres.dstemonnqalpvwgzvfto',
    password='Cai@927929#',
    sslmode='require'
)
conn.autocommit = True
cur = conn.cursor()

cur.execute("UPDATE pricing_data SET marca='NIKE'   WHERE marca IN ('Nike','nike')")
print('NIKE updated:', cur.rowcount)

cur.execute("UPDATE pricing_data SET marca='ADIDAS' WHERE marca IN ('Adidas','adidas')")
print('ADIDAS updated:', cur.rowcount)

cur.execute("UPDATE pricing_data SET marca='PUMA'   WHERE marca IN ('Puma','puma')")
print('PUMA updated:', cur.rowcount)

cur.execute("SELECT marca, COUNT(*) FROM pricing_data GROUP BY marca ORDER BY COUNT(*) DESC")
print('\nFinal:')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]:,}')

conn.close()
print('Done')
