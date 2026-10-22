import type { ModelOption } from "./api";
import s from "./connect.module.css";

export type ModelPickerProps = {
  /** The models the connection can use (verified ones first), or null while they load. */
  models: ModelOption[] | null;
  /** The chosen model id, or "" when none is chosen. */
  value: string;
  onChange: (model: string) => void;
  /** Why the list is empty ("Loading models…", "Connect first"), shown in place of the list. */
  note?: string | null | undefined;
  disabled?: boolean | undefined;
  label?: string | undefined;
  className?: string | undefined;
};

/** A model select. It never fills in a model by itself: no choice stays "Choose a model". Models
 * the operator has not verified are marked. */
export function ModelPicker({
  models,
  value,
  onChange,
  note,
  disabled,
  label = "Model",
  className,
}: ModelPickerProps) {
  if (!models?.length) {
    return (
      <label className={s.field}>
        <span className={s.label}>{label}</span>
        <input
          className={s.input}
          disabled
          value=""
          placeholder={note ?? "connect first, then pick a model"}
          readOnly
        />
      </label>
    );
  }
  // Only mark models unverified when the operator verified some: with no table, nothing is.
  const marking = models.some((m) => m.verified);
  return (
    <label className={s.field}>
      <span className={s.label}>{label}</span>
      <select
        className={className ?? s.input}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Choose a model</option>
        {models.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label}
            {marking && !m.verified ? " (unverified)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
