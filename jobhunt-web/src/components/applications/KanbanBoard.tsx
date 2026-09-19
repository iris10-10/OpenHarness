import type { ApplicationRecord, ApplicationStatus } from "../../types";
import { APPLICATION_STATUSES } from "../../utils/constants";
import KanbanColumn from "./KanbanColumn";

export default function KanbanBoard({
  records,
  onMove,
  onDelete,
}: {
  records: ApplicationRecord[];
  onMove: (id: string, status: ApplicationStatus) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <div className="kanban">
      {APPLICATION_STATUSES.map((status) => (
        <KanbanColumn
          key={status}
          status={status}
          records={records.filter((record) => record.status === status)}
          onMove={onMove}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
