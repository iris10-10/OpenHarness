import { useEffect, useRef } from "react";
import * as echarts from "echarts";

export default function PieChart({ data }: { data: Record<string, number> }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      tooltip: { trigger: "item" },
      series: [{ type: "pie", radius: ["45%", "70%"], data: Object.entries(data).map(([name, value]) => ({ name, value })) }],
    });
    return () => chart.dispose();
  }, [data]);
  return <div ref={ref} className="chart" />;
}
