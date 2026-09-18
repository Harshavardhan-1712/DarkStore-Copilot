"""Seed a demo dark store: a dairy aisle with a planned stockout, plus staples and snacks.

    python infra/seed_data.py --table darkstore-copilot-inventory --region ap-south-1
"""
import argparse
from decimal import Decimal

import boto3

CATALOG = [
    # sku_id, name, category, aisle, shelf, stock, price, substitutes
    ("SKU_MILK_AMUL_500", "Amul Taaza Milk 500ml", "dairy", "A3", "S2", 0, "32", ["SKU_MILK_NANDINI_500", "SKU_MILK_HERITAGE_500", "SKU_MILK_AMUL_1L"]),
    ("SKU_MILK_NANDINI_500", "Nandini Milk 500ml", "dairy", "A3", "S2", 24, "31", ["SKU_MILK_AMUL_500"]),
    ("SKU_MILK_HERITAGE_500", "Heritage Milk 500ml", "dairy", "A3", "S3", 9, "34", ["SKU_MILK_AMUL_500"]),
    ("SKU_MILK_AMUL_1L", "Amul Taaza Milk 1L", "dairy", "A3", "S3", 12, "64", ["SKU_MILK_AMUL_500"]),
    ("SKU_CURD_400", "Amul Curd 400g", "dairy", "A3", "S1", 18, "30", ["SKU_CURD_DODLA_400"]),
    ("SKU_CURD_DODLA_400", "Dodla Curd 400g", "dairy", "A3", "S1", 15, "29", ["SKU_CURD_400"]),
    ("SKU_RICE_SONA_5KG", "Sona Masoori Rice 5kg", "staples", "A1", "S4", 22, "410", ["SKU_RICE_BPT_5KG"]),
    ("SKU_RICE_BPT_5KG", "BPT Rice 5kg", "staples", "A1", "S4", 8, "430", ["SKU_RICE_SONA_5KG"]),
    ("SKU_OIL_SUN_1L", "Sunflower Oil 1L", "staples", "A2", "S1", 30, "145", ["SKU_OIL_RICE_1L"]),
    ("SKU_OIL_RICE_1L", "Rice Bran Oil 1L", "staples", "A2", "S2", 11, "159", ["SKU_OIL_SUN_1L"]),
    ("SKU_ATTA_5KG", "Whole Wheat Atta 5kg", "staples", "A1", "S2", 14, "265", []),
    ("SKU_BISCUIT_PARLE", "Parle-G 800g", "snacks", "A4", "S3", 40, "95", ["SKU_BISCUIT_MARIE"]),
    ("SKU_BISCUIT_MARIE", "Marie Gold 600g", "snacks", "A4", "S3", 26, "92", ["SKU_BISCUIT_PARLE"]),
    ("SKU_CHIPS_LAYS", "Lay's Classic 52g", "snacks", "A4", "S1", 60, "20", []),
    ("SKU_PARA_500", "Paracetamol 500mg strip", "pharma", "A6", "S1", 12, "25", ["SKU_PARA_650"]),
    ("SKU_PARA_650", "Paracetamol 650mg strip", "pharma", "A6", "S1", 20, "30", ["SKU_PARA_500"]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--region", default="ap-south-1")
    args = ap.parse_args()

    table = boto3.resource("dynamodb", region_name=args.region).Table(args.table)
    with table.batch_writer() as batch:
        for sku, name, cat, aisle, shelf, stock, price, subs in CATALOG:
            batch.put_item(
                Item={
                    "sku_id": sku,
                    "name": name,
                    "category": cat,
                    "aisle": aisle,
                    "shelf": shelf,
                    "stock_qty": stock,
                    "price": Decimal(price),
                    "substitution_skus": subs,
                    "active": True,
                }
            )
    print("Seeded %d SKUs into %s" % (len(CATALOG), args.table))
    print("Demo stockout: SKU_MILK_AMUL_500 has 0 on hand — say \"Amul milk ledu\" to trigger substitution.")
    print("Policy demo: pharma SKUs are substitution-blocked and will be refused.")


if __name__ == "__main__":
    main()
