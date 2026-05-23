import { Calendar, CheckCircle2, Clock } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { useTodayAppointments, useCheckInAppointment } from "../hooks/useAppointments";
import { useNavigate } from "react-router-dom";

export function AppointmentsDashboard() {
  const navigate = useNavigate();
  const { data: appointments, isLoading } = useTodayAppointments();
  const checkIn = useCheckInAppointment();

  const statusVariant = (s: string): "success" | "warning" | "secondary" | "info" => {
    switch (s?.toLowerCase()) {
      case "checkedin": return "success";
      case "scheduled": return "info";
      case "missed": return "warning";
      default: return "secondary";
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">Today's Appointments</h1>
        <Badge variant="info">
          <Calendar size={13} className="mr-1" /> {appointments?.length ?? 0} scheduled
        </Badge>
      </div>

      <Card>
        {isLoading ? (
          <CardContent className="space-y-2 p-4">
            {Array(5).fill(0).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}
          </CardContent>
        ) : !appointments || appointments.length === 0 ? (
          <CardContent className="py-12 text-center text-sm text-[hsl(var(--muted-foreground))]">
            No appointments scheduled for today.
          </CardContent>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="text-xs">Time</TableHead>
                <TableHead className="text-xs">Patient</TableHead>
                <TableHead className="text-xs">Service</TableHead>
                <TableHead className="text-xs">Status</TableHead>
                <TableHead></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {appointments.map((appt) => (
                <TableRow
                  key={appt.uuid}
                  className="cursor-pointer hover:bg-[var(--clinic-ice)]"
                  onClick={() => navigate(`/patients/${appt.patient.uuid}`)}
                >
                  <TableCell className="text-xs font-medium">
                    <span className="flex items-center gap-1">
                      <Clock size={12} />
                      {new Date(appt.startDateTime).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </span>
                  </TableCell>
                  <TableCell className="font-medium text-[var(--clinic-ink)]">{appt.patient.display}</TableCell>
                  <TableCell className="text-sm text-[var(--clinic-slate)]">{appt.service.display}</TableCell>
                  <TableCell>
                    <Badge variant={statusVariant(appt.status)} className="text-xs">{appt.status}</Badge>
                  </TableCell>
                  <TableCell>
                    {appt.status?.toLowerCase() === "scheduled" && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        onClick={(e) => {
                          e.stopPropagation();
                          checkIn.mutate({ uuid: appt.uuid });
                        }}
                      >
                        <CheckCircle2 size={12} className="mr-1" /> Check In
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </div>
  );
}
