import { useForm } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useRecordVitals, VITAL_CONCEPTS } from "../hooks/useClinical";
import { Save } from "lucide-react";

interface Props {
  patientUuid: string;
  visitUuid: string;
  locationUuid: string;
  onSuccess: () => void;
}

interface VitalsFormData {
  temperature: string;
  systolicBP: string;
  diastolicBP: string;
  pulse: string;
  oxygenSat: string;
  respRate: string;
  height: string;
  weight: string;
}

export function RecordVitalsForm({ patientUuid, visitUuid, locationUuid, onSuccess }: Props) {
  const { register, handleSubmit } = useForm<VitalsFormData>();
  const recordVitals = useRecordVitals();

  const onSubmit = async (data: VitalsFormData) => {
    const obs: Array<{ concept: string; value: number }> = [];
    const addObs = (concept: string, val: string) => {
      const num = parseFloat(val);
      if (!Number.isNaN(num) && num > 0) obs.push({ concept, value: num });
    };

    addObs(VITAL_CONCEPTS.temperature, data.temperature);
    addObs(VITAL_CONCEPTS.systolicBP, data.systolicBP);
    addObs(VITAL_CONCEPTS.diastolicBP, data.diastolicBP);
    addObs(VITAL_CONCEPTS.pulse, data.pulse);
    addObs(VITAL_CONCEPTS.oxygenSat, data.oxygenSat);
    addObs(VITAL_CONCEPTS.respRate, data.respRate);
    addObs(VITAL_CONCEPTS.height, data.height);
    addObs(VITAL_CONCEPTS.weight, data.weight);

    if (obs.length === 0) return;

    await recordVitals.mutateAsync({
      patient: patientUuid,
      visit: visitUuid,
      encounterDatetime: new Date().toISOString(),
      location: locationUuid,
      obs,
    });
    onSuccess();
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label>Temperature (°C)</Label>
          <Input type="number" step="0.1" placeholder="36.5" {...register("temperature")} />
        </div>
        <div className="space-y-1.5">
          <Label>Pulse (bpm)</Label>
          <Input type="number" placeholder="72" {...register("pulse")} />
        </div>
        <div className="space-y-1.5">
          <Label>Systolic BP (mmHg)</Label>
          <Input type="number" placeholder="120" {...register("systolicBP")} />
        </div>
        <div className="space-y-1.5">
          <Label>Diastolic BP (mmHg)</Label>
          <Input type="number" placeholder="80" {...register("diastolicBP")} />
        </div>
        <div className="space-y-1.5">
          <Label>SpO2 (%)</Label>
          <Input type="number" placeholder="98" {...register("oxygenSat")} />
        </div>
        <div className="space-y-1.5">
          <Label>Respiratory Rate</Label>
          <Input type="number" placeholder="16" {...register("respRate")} />
        </div>
        <div className="space-y-1.5">
          <Label>Height (cm)</Label>
          <Input type="number" step="0.1" placeholder="170" {...register("height")} />
        </div>
        <div className="space-y-1.5">
          <Label>Weight (kg)</Label>
          <Input type="number" step="0.1" placeholder="70" {...register("weight")} />
        </div>
      </div>
      <Button type="submit" className="w-full" disabled={recordVitals.isPending}>
        {recordVitals.isPending ? "Saving..." : <><Save size={14} className="mr-1.5" /> Save Vitals</>}
      </Button>
    </form>
  );
}
