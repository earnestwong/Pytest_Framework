# -*- coding: utf-8 -*-
"""
串联两个脚本，自动批量补采全部 23 家店铺最近 60 天的差评（含商家回复及回复日期）：
  1. 店铺导航:  nav_search_negative.navigate_to_negative_list(kw)
              从首页搜索并进入目标店铺的「差评」列表顶部
  2. 增量采集:  子进程运行 collect_native_incr.py --cutoff-days 60
              从差评列表顶部往下采集，遇到 review_date < 今天-60天 的旧评论即停止，
              按 hash 去重入库 store_reviews_negative，并输出 CSV/JSON。

单店失败不中断整批，继续下一家；校验码时暂停等待人工处理。
数据库可用即入库（store_reviews_negative）；不可用仅输出 CSV/JSON（外网场景）。

用法：
    py -3.10 -u negative_incr_all_60day.py                 # 从第 1 家开始
    py -3.10 -u negative_incr_all_60day.py <start_index>   # 从第 N 家开始(续跑)
"""
import sys
import os
import csv
import time
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from _test_nav_all import SHOPS          # 23 家店铺清单 (org_code, store_name, kw)

CUTOFF_DAYS = 60                         # 采集最近 60 天差评(边界=今天-60天)
CAPTURE_SCRIPT = os.path.join(BASE, "collect_native_incr.py")
OUT_CSV = os.path.join(BASE, "_negative_incr_all_results.csv")


def run_capture(org_code: str, shop_name: str) -> tuple:
    """子进程运行 collect_native_incr.py 采集该店最近 60 天差评。
    脚本自身具备增量停止/去重入库/CSV/JSON 输出。返回 (成功?, 备注)。"""
    cmd = [sys.executable, "-u", CAPTURE_SCRIPT,
           "--shop", shop_name, "--org-code", org_code,
           "--cutoff-days", str(CUTOFF_DAYS)]
    try:
        # 不捕获输出,子进程 print 实时流到同一控制台
        rc = subprocess.run(cmd, cwd=BASE).returncode
        return rc == 0, f"returncode={rc}"
    except Exception as e:
        return False, f"子进程异常: {e!r}"


def main():
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    import nav_search_negative as nav
    nav.init()  # 复用导航全局 adb(与 _test_nav_all 一致)

    results = []
    print("=" * 64)
    print(f"批量补采全部 {len(SHOPS)} 家店铺最近 {CUTOFF_DAYS} 天差评"
          f" (Start from index {start + 1})")
    print("=" * 64)

    for idx, (org, name, kw) in enumerate(SHOPS, 1):
        if idx - 1 < start:
            continue
        print(f"\n##### [{idx}/{len(SHOPS)}] org={org} | {name} | kw={kw} #####",
              flush=True)

        # --- 1. 导航到差评列表顶部 ---
        try:
            r = nav.navigate_to_negative_list(kw, do_init=False, verbose=True)
        except Exception as e:
            r = {"ok": False, "shop_name": "", "matched": False,
                 "err": f"导航异常: {e!r}"}

        if not r["ok"]:
            results.append((idx, org, name, "NAV_FAIL", r.get("err", "")))
            print(f"  [NAV_FAIL] {r.get('err','')}", flush=True)
            if "验证码" in r.get("err", ""):
                print("  !!! 遇到验证码,请人工处理; 10 秒后重试本店", flush=True)
                time.sleep(10)
                try:
                    r2 = nav.navigate_to_negative_list(kw, do_init=False, verbose=True)
                except Exception as e:
                    r2 = {"ok": False, "shop_name": "", "err": f"导航异常: {e!r}"}
                if not r2["ok"]:
                    results[-1] = (idx, org, name, "NAV_FAIL", r2.get("err", ""))
                    print(f"  [retry NAV_FAIL] {r2.get('err','')}", flush=True)
                    continue
                print("  [retry] 重试导航成功", flush=True)
            else:
                continue

        # --- 2. 采集最近 60 天差评(子进程) ---
        print(f"  >>> 开始采集 {name} 最近 {CUTOFF_DAYS} 天差评...", flush=True)
        ok, note = run_capture(org, name)
        status = "DONE" if ok else "CAP_FAIL"
        results.append((idx, org, name, status, note))
        print(f"  [{status}] {name}: {note}", flush=True)

    # --- 汇总写 CSV ---
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["index", "org_code", "store_name", "status", "note"])
        w.writerows(results)

    print("\n\n========== 批量补采汇总 ==========")
    ndone = sum(1 for _, _, _, s, _ in results if s == "DONE")
    for idx, org, name, status, note in results:
        print(f"  [{status}] #{idx} {org} | {name} | {note}")
    print(f"\n合计 {len(results)} 家, 采集完成 {ndone} 家, "
          f"失败 {len(results) - ndone} 家")
    print(f"结果已写入: {OUT_CSV}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[中断] 已手动停止(已采集店铺的结果已落库/CSV)")
        sys.exit(1)