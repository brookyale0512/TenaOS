import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Calendar, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Workspace } from "@/components/workspace";
import { useAppointmentServices, useCreateAppointment } from "../hooks/useAppointments";
import { useLocations } from "@/features/patients/hooks/usePatients";
import { openmrsRuntimeConfig } from "@/lib/openmrs/runtimeConfig";

interface AddAppointmentWorkspaceProps {
  open: boolean;
  onClose: () => void;
  patientUuid: string;
}

function isToday(dateStr: string): boolean {
  const today = new Date().toISOString().split("T")[0];
  return dateStr === today;
}

export function AddAppointmentWorkspace({
  open,
  onClose,
  patientUuid,
}: AddAppointmentWorkspaceProps) {
  const navigate = useNavigate();
  const createAppointment = useCreateAppointment();
  const { data: services } = useAppointmentServices();
  const { data: locations } = useLocations();

  const [serviceUuid, setServiceUuid] = useState("");
  const [date, setDate] = useState(() => new Date().toISOString().split("T")[0]);
  const [time, setTime] = useState("09:00");
  const [locationUuid, setLocationUuid] = useState("");
  const [kind, setKind] = useState<"Scheduled" | "WalkIn">("Scheduled");
  const [comments, setComments] = useState("");

  // Derive effective location: if only one location available and user hasn't selected one, use it
  const effectiveLocationUuid = locationUuid || (locations?.length === 1 ? locations[0].uuid : "");

  const selectedService = services?.find((s) => s.uuid === serviceUuid);
  const appointmentIsToday = isToday(date);
  const queuesEnabled = openmrsRuntimeConfig.capabilities.queues;

  const computeTimes = () => {
    const [h, m] = time.split(":").map(Number);
    const start = new Date(`${date}T${time}:00`);
    const end = new Date(start.getTime() + (selectedService?.durationMins ?? 30) * 60_000);
    const endH = String(end.getHours()).padStart(2, "0");
    const endM = String(end.getMinutes()).padStart(2, "0");
    return {
      startMs: start.getTime(),
      endMs: end.getTime(),
      startLabel: `${h}:${String(m).padStart(2, "0")} ${h >= 12 ? "PM" : "AM"}`,
      endLabel: `${endH}:${endM}`,
    };
  };

  const handleSubmit = async () => {
    if (!serviceUuid || !date || !time) return;
    const { startMs, endMs } = computeTimes();
    await createAppointment.mutateAsync({
      patientUuid,
      serviceUuid,
      startDateTime: startMs,
      endDateTime: endMs,
      appointmentKind: kind,
      locationUuid: effectiveLocationUuid || undefined,
      comments: comments.trim() || undefined,
    });

    // Reset form
    setServiceUuid("");
    setDate(new Date().toISOString().split("T")[0]);
    setTime("09:00");
    setLocationUuid("");
    setKind("Scheduled");
    setComments("");
    onClose();
  };

  const canSubmit = !!serviceUuid && !!date && !!time && !createAppointment.isPending;
  const { startLabel, endLabel } = serviceUuid && time && date ? computeTimes() : { startLabel: "", endLabel: "" };

  return (
    <Workspace
      open={open}
      onClose={onClose}
      title="Book Appointment"
      subtitle="Schedule a future appointment for this patient."
    >
      <div className="space-y-4">
        {/* Appointment kind */}
        <div className="space-y-1.5">
          <Label>Appointment type</Label>
          <div className="flex gap-2">
            {(["Scheduled", "WalkIn"] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setKind(k)}
                className={[
                  "flex-1 rounded-lg border py-2 text-sm font-medium transition-colors",
                  kind === k
                    ? "border-[var(--clinic-blue)] bg-blue-50 text-[var(--clinic-blue)]"
                    : "border-[var(--clinic-border)] bg-white text-[hsl(var(--muted-foreground))] hover:bg-[var(--clinic-ice)]",
                ].join(" ")}
              >
                {k === "WalkIn" ? "Walk-In" : "Scheduled"}
              </button>
            ))}
          </div>
        </div>

        {/* Service */}
        <div className="space-y-1.5">
          <Label>Service <span className="text-red-500">*</span></Label>
          <select
            value={serviceUuid}
            onChange={(e) => setServiceUuid(e.target.value)}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <option value="">Select service</option>
            {services?.map((s) => (
              <option key={s.uuid} value={s.uuid}>
                {s.name}
                {s.durationMins ? ` (${s.durationMins} min)` : ""}
              </option>
            ))}
          </select>
        </div>

        {/* Date + time */}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label>Date <span className="text-red-500">*</span></Label>
            <Input
              type="date"
              value={date}
              min={new Date().toISOString().split("T")[0]}
              onChange={(e) => setDate(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Start time <span className="text-red-500">*</span></Label>
            <Input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
          </div>
        </div>

        {/* Time summary */}
        {selectedService && startLabel && (
          <div className="flex items-center gap-2 rounded-lg bg-[var(--clinic-ice)] border px-3 py-2 text-xs text-[hsl(var(--muted-foreground))]">
            <Calendar size={12} />
            <span>
              {startLabel} → {endLabel}
              {" · "}
              {selectedService.durationMins} min · {selectedService.name}
            </span>
          </div>
        )}

        {/* Location */}
        <div className="space-y-1.5">
          <Label>Location</Label>
          <select
            value={locationUuid}
            onChange={(e) => setLocationUuid(e.target.value)}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <option value="">Any location</option>
            {locations?.map((loc: { uuid: string; display: string }) => (
              <option key={loc.uuid} value={loc.uuid}>
                {loc.display}
              </option>
            ))}
          </select>
        </div>

        {/* Comments */}
        <div className="space-y-1.5">
          <Label>Notes (optional)</Label>
          <Textarea
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            placeholder="Reason for visit, special instructions..."
            className="min-h-[72px]"
          />
        </div>

        {/* Today's appointment hint */}
        {appointmentIsToday && queuesEnabled && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
            This appointment is for today. After booking, you can add the patient to the queue from the{" "}
            <button
              type="button"
              className="underline font-medium"
              onClick={() => {
                onClose();
                navigate("/queues");
              }}
            >
              Queues
            </button>{" "}
            page.
          </div>
        )}

        <Button className="w-full" onClick={handleSubmit} disabled={!canSubmit}>
          {createAppointment.isPending ? (
            "Booking..."
          ) : (
            <>
              <Save size={14} className="mr-1" /> Book Appointment
            </>
          )}
        </Button>
      </div>
    </Workspace>
  );
}
