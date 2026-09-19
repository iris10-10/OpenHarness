import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export default function TrendChart({ data }: { data: Array<{ date: string; count: number }> }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      tooltip: {},
      xAxis: { type: "category", data: data.map((item) => item.date || "-") },
      yAxis: { type: "value" },
      series: [{ type: "line", smooth: true, data: data.map((item) => item.count) }],
    });
    return () => chart.dispose();
  }, [data]);
  return <div ref={ref} className="chart" />;
}
