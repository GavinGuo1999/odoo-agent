"""给 odoo19_dev 造演示用销售数据，覆盖最近 12 个月。

**必须经 Odoo ORM（XML-RPC）写入，不能直接 SQL INSERT。**直接插表会绕过单号
序列、`amount_untaxed` 等计算字段和状态机，造出金额对不上的记录——那种数据会
反过来污染黄金集与 A/B 基准，比没有数据更糟。

所有生成的记录都带 `DEMO-<批次号>` 前缀写在 `origin` 上，便于事后整批查找和删除。

用法（先启动 Docker/Odoo）：

    # 只看计划，不写库
    .venv\\Scripts\\python.exe tools\\seed_demo_data.py --dry-run

    # 真正写入
    .venv\\Scripts\\python.exe tools\\seed_demo_data.py --orders 240 \\
        --url http://127.0.0.1:8069 --db odoo19_dev --user admin

密码从 `--password` 或环境变量 `ODOO_ADMIN_PASSWORD` 读取，不落盘、不打印。
"""

from __future__ import annotations

import argparse
import calendar
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable


DEFAULT_URL = "http://127.0.0.1:8069"
DEFAULT_DB = "odoo19_dev"

# 每单行数与数量/折扣的取值范围：贴近真实小单为主、偶有大单的分布。
LINES_PER_ORDER = (1, 4)
QTY_RANGE = (1, 12)

# 状态分布：绝大多数是已确认的正常单，留少量报价与取消单，
# 这样"未确认订单""取消率"这类问题才有数据可查。
STATE_WEIGHTS = (("sale", 72), ("done", 12), ("draft", 10), ("cancel", 6))


@dataclass(frozen=True, slots=True)
class OrderLinePlan:
    product_index: int
    quantity: int


@dataclass(frozen=True, slots=True)
class OrderPlan:
    order_date: date
    customer_index: int
    state: str
    lines: tuple[OrderLinePlan, ...]


@dataclass
class SeedPlan:
    orders: list[OrderPlan] = field(default_factory=list)

    def month_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for order in self.orders:
            key = order.order_date.strftime("%Y-%m")
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def state_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for order in self.orders:
            counts[order.state] = counts.get(order.state, 0) + 1
        return dict(sorted(counts.items()))


def month_starts(today: date, months: int) -> list[date]:
    """返回最近 `months` 个自然月的月初，最后一个是 today 所在月。"""

    starts: list[date] = []
    year, month = today.year, today.month
    for _ in range(months):
        starts.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(starts))


def _weighted_state(rng: random.Random) -> str:
    total = sum(weight for _, weight in STATE_WEIGHTS)
    roll = rng.randrange(total)
    cumulative = 0
    for state, weight in STATE_WEIGHTS:
        cumulative += weight
        if roll < cumulative:
            return state
    return STATE_WEIGHTS[0][0]


def build_plan(
    *,
    today: date,
    total_orders: int,
    months: int,
    customer_count: int,
    product_count: int,
    seed: int = 20260907,
) -> SeedPlan:
    """把订单铺到最近 `months` 个月，每个月都非空。

    当月按已过天数比例缩减——当月才过了几天却和整月一样多，看趋势图时会显得
    突然放量，是假信号。
    """

    if total_orders < months:
        raise ValueError(f"总单量 {total_orders} 少于月份数 {months}，无法保证每月非空")
    if customer_count < 1 or product_count < 1:
        raise ValueError("客户与产品数量都必须至少为 1")

    rng = random.Random(seed)
    starts = month_starts(today, months)

    # 权重随月份线性上升，模拟业务增长；当月按进度折算。
    weights: list[float] = []
    for index, start in enumerate(starts):
        weight = 1.0 + 0.6 * index / max(months - 1, 1)
        if start.year == today.year and start.month == today.month:
            days_in_month = calendar.monthrange(today.year, today.month)[1]
            weight *= max(today.day, 1) / days_in_month
        weights.append(weight)

    # 先保底每月一单，再按权重分配剩余，确保没有空月。
    counts = [1] * months
    remaining = total_orders - months
    total_weight = sum(weights)
    for index, weight in enumerate(weights):
        counts[index] += int(remaining * weight / total_weight)
    for index in range(total_orders - sum(counts)):
        counts[index % months] += 1

    orders: list[OrderPlan] = []
    for start, count in zip(starts, counts):
        days_in_month = calendar.monthrange(start.year, start.month)[1]
        last_day = today.day if (start.year, start.month) == (today.year, today.month) else days_in_month
        for _ in range(count):
            order_date = start + timedelta(days=rng.randrange(max(last_day, 1)))
            line_count = rng.randint(*LINES_PER_ORDER)
            products = rng.sample(range(product_count), min(line_count, product_count))
            orders.append(
                OrderPlan(
                    order_date=order_date,
                    customer_index=rng.randrange(customer_count),
                    state=_weighted_state(rng),
                    lines=tuple(
                        OrderLinePlan(product_index=p, quantity=rng.randint(*QTY_RANGE))
                        for p in products
                    ),
                )
            )
    orders.sort(key=lambda order: order.order_date)
    return SeedPlan(orders=orders)


def _connect(url: str, db: str, user: str, password: str) -> tuple[Any, int]:
    import xmlrpc.client

    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, user, password, {})
    if not uid:
        raise SystemExit("认证失败：请确认 --db / --user 与密码正确")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return models, uid


class OdooWriter:
    def __init__(self, models: Any, db: str, uid: int, password: str) -> None:
        self._models, self._db, self._uid, self._password = models, db, uid, password

    def call(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._models.execute_kw(
            self._db, self._uid, self._password, model, method, list(args), kwargs
        )

    def search_read(self, model: str, domain: list, fields: list[str], limit: int = 0) -> list[dict]:
        return self.call(model, "search_read", domain, fields=fields, limit=limit)


def ensure_customers(writer: OdooWriter, batch: str, wanted: int) -> list[int]:
    existing = writer.search_read(
        "res.partner", [("customer_rank", ">", 0), ("active", "=", True)], ["id"], limit=wanted
    )
    ids = [row["id"] for row in existing]
    names = [
        "启明科技", "远航物流", "京华建材", "南岭食品", "海塘电子", "云栖软件",
        "长风机械", "青柏医疗", "沐阳纺织", "恒诚化工", "锦程贸易", "白鹭包装",
    ]
    while len(ids) < wanted:
        index = len(ids)
        ids.append(
            writer.call(
                "res.partner",
                "create",
                {
                    "name": f"{names[index % len(names)]}（{batch}）",
                    "customer_rank": 1,
                    "company_type": "company",
                    "comment": f"演示数据 {batch}",
                },
            )
        )
    return ids


CATALOGUE = [
    ("工业传感器 S200", 480.0), ("变频控制器 C15", 1250.0), ("伺服电机 M8", 2380.0),
    ("精密轴承套件", 320.0), ("工控触摸屏 10寸", 1680.0), ("安全继电器", 260.0),
    ("光电开关 E3", 140.0), ("线缆组件 5m", 95.0),
]

# 复用现有产品前的最低定价门槛。库里遗留了一批 list_price = 1.00 的开发测试件，
# 用它们下单会造出“整月销售额 3 元”这种数字，趋势和排名就全是噪声。
MIN_REUSABLE_PRICE = 20.0


def ensure_products(writer: OdooWriter, batch: str, wanted: int) -> list[int]:
    """优先复用定价合理的现有产品，不够的部分按内置目录新建。

    只看 `sale_ok` 是不够的：必须同时要求 `list_price` 达到门槛，否则会把
    历史遗留的 1 元测试件当成商品。
    """

    existing = writer.search_read(
        "product.product",
        [
            ("sale_ok", "=", True),
            ("active", "=", True),
            ("list_price", ">=", MIN_REUSABLE_PRICE),
        ],
        ["id", "list_price"],
        limit=wanted,
    )
    ids = [row["id"] for row in existing]
    while len(ids) < wanted:
        name, price = CATALOGUE[len(ids) % len(CATALOGUE)]
        ids.append(
            writer.call(
                "product.product",
                "create",
                {
                    "name": f"{name}（{batch}）",
                    "list_price": price,
                    "standard_price": round(price * 0.62, 2),
                    "sale_ok": True,
                    "type": "consu",
                },
            )
        )
    return ids


def apply_plan(
    writer: OdooWriter,
    plan: SeedPlan,
    *,
    batch: str,
    customer_ids: list[int],
    product_ids: list[int],
    progress_every: int = 25,
) -> dict[str, int]:
    created = 0
    confirmed = 0
    cancelled = 0
    for index, order in enumerate(plan.orders, start=1):
        order_id = writer.call(
            "sale.order",
            "create",
            {
                "partner_id": customer_ids[order.customer_index % len(customer_ids)],
                "date_order": datetime.combine(
                    order.order_date, datetime.min.time().replace(hour=10)
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "origin": f"{batch}",
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": product_ids[line.product_index % len(product_ids)],
                            "product_uom_qty": line.quantity,
                        },
                    )
                    for line in order.lines
                ],
            },
        )
        created += 1
        if order.state in {"sale", "done"}:
            writer.call("sale.order", "action_confirm", [order_id])
            confirmed += 1
            # 确认动作会把 date_order 推到当前时间，必须再写回目标日期，
            # 否则所有订单都会堆在今天，趋势类问题就没有意义了。
            writer.call(
                "sale.order",
                "write",
                [order_id],
                {
                    "date_order": datetime.combine(
                        order.order_date, datetime.min.time().replace(hour=10)
                    ).strftime("%Y-%m-%d %H:%M:%S")
                },
            )
        elif order.state == "cancel":
            writer.call("sale.order", "action_cancel", [order_id])
            cancelled += 1
        if progress_every and index % progress_every == 0:
            print(f"  已创建 {index}/{len(plan.orders)}", flush=True)
    return {"created": created, "confirmed": confirmed, "cancelled": cancelled}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="给 Odoo 造覆盖最近 12 个月的演示销售数据。")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default=os.getenv("ODOO_ADMIN_PASSWORD"))
    parser.add_argument("--orders", type=int, default=240)
    parser.add_argument("--months", type=int, default=12)
    parser.add_argument("--customers", type=int, default=12)
    parser.add_argument("--products", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不连接 Odoo、不写库。")
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan = build_plan(
        today=date.today(),
        total_orders=args.orders,
        months=args.months,
        customer_count=args.customers,
        product_count=args.products,
        seed=args.seed,
    )

    print(f"计划创建 {len(plan.orders)} 张订单，覆盖 {args.months} 个月")
    print("按月分布：")
    for month, count in plan.month_counts().items():
        print(f"  {month}  {count:4d}  {'▇' * min(count, 60)}")
    print(f"按状态：{plan.state_counts()}")

    if args.dry_run:
        print("\n--dry-run：未连接 Odoo，未写入任何数据。")
        return 0

    if not args.password:
        parser.error("需要密码：用 --password 或设置环境变量 ODOO_ADMIN_PASSWORD")

    batch = f"DEMO-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    print(f"\n批次号 {batch}（写在每张订单的 origin 上，便于整批清理）")
    models, uid = _connect(args.url, args.db, args.user, args.password)
    writer = OdooWriter(models, args.db, uid, args.password)

    customer_ids = ensure_customers(writer, batch, args.customers)
    product_ids = ensure_products(writer, batch, args.products)
    print(f"客户 {len(customer_ids)} 个，产品 {len(product_ids)} 个")

    summary = apply_plan(
        writer, plan, batch=batch, customer_ids=customer_ids, product_ids=product_ids
    )
    print(f"\n完成：{summary}")
    print(f"清理命令：在 Odoo 里按 origin = '{batch}' 搜索销售订单后批量删除。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
