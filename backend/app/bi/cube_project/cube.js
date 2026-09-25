// Cube 配置：只读、不建预聚合。
//
// 两条必须守住的约束，写在这里而不是环境变量里，免得换个终端就丢：
//
// 1. **数据库账号必须是只读的**（codex_readonly）。Cube 自己连库，
//    所以项目那道 `default_transaction_read_only=on` 的连接串防线对它不生效——
//    唯一的防线就是账号本身没有写权限。
//
// 2. **不能让 Cube 建预聚合。** 它默认会往一个 schema 里建表，只读账号下必然失败。
//    本对照只要「把语义查询编译成 SQL」这一个能力，预聚合是纯负担。
//    做法是：模型里一个 `pre_aggregations` 都不声明，并关掉调度刷新。
//    （不要在 preAggregationsSchema 里抛异常——Cube 在编译期就会调它，会直接起不来。）
//
// Cube 1.7 起 `dbType` 已移除，必须由 driverFactory 返回 DriverConfig。

module.exports = {
  driverFactory: () => ({
    type: 'postgres',
    host: process.env.ODOO_DB_HOST || '127.0.0.1',
    port: Number(process.env.ODOO_DB_PORT || 55432),
    database: process.env.ODOO_DB_NAME || 'odoo19_dev',
    user: process.env.ODOO_DB_USER || 'codex_readonly',
    password: process.env.ODOO_DB_PASSWORD || undefined,
    ssl: false,

  }),

  // 关掉调度刷新，否则 Cube 会后台尝试构建预聚合。
  scheduledRefreshTimer: false,

  // 即便有人误加了预聚合，也让它落在一个明显是临时的名字上，便于在日志里发现。
  preAggregationsSchema: 'odoo_agent_probe_must_not_be_created',
};
