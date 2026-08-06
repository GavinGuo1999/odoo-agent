"""Local application services."""

from .windows_environment import set_user_environment
from .sales_dashboard import SalesDashboardService

__all__ = ["SalesDashboardService", "set_user_environment"]
