# -*- coding: utf-8 -*-
"""Deprecated: provider ratings must not be copied into the reusable POI master."""


def main():
    raise SystemExit(
        "已停用：高德评分缺少可持久化授权与逐条审计记录，不能写回 data/pois.json。"
        "如需展示评分，应在获得相应用途许可后通过线上 API 实时查询，并注明来源与时间。"
    )


if __name__ == "__main__":
    main()
