# Odoo 销售分析口径

## 公司与订单状态

- 每条销售事实查询必须使用上下文中的 `required_company_id` 过滤 `company_id`。
- 销售订单仅统计 `sale_order.state IN ('sale', 'done')`。

## 指标

- “销售额”默认指 `SUM(sale_order.amount_untaxed)`，即未税销售额。
- 产品销售额使用 `SUM(sale_order_line.price_subtotal)`。
- 订单数使用 `COUNT(DISTINCT sale_order.id)`。
- 平均订单额为销售额除以去重订单数。
- `SUM` 等可能无行的聚合使用 `COALESCE(..., 0)`。
- 产品销售数量、交付数量、开票数量分别使用 `product_uom_qty`、`qty_delivered`、`qty_invoiced`。
- 未开票数量差额使用 `SUM(product_uom_qty - qty_invoiced)`；它表示订购数量减已开票数量，不等同于 `qty_to_invoice`。
- 产品分析必须排除 `sale_order_line.display_type IS NOT NULL` 的展示行。

## 名称与时间

- 产品名优先读取 `product_template.name->>'zh_CN'`，缺失时读取 `en_US`。
- 销售员名通过 `sale_order.user_id -> res_users.partner_id -> res_partner.name` 获取。
- 时间问题使用 `sale_order.date_order` 和数据库 `CURRENT_DATE`。

## 安全

- 只允许一条只读 SELECT 或只读 CTE，不得生成 DDL、DML、事务或系统目录查询。
- 明细查询最多返回 500 行；用户明确要求 Top N 时严格遵守 N。只要求“各项排名”而未指定 N 时返回完整排序，由系统安全行数上限兜底，图表只展示前 10。
