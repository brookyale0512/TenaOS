import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { UserPlus, RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Card } from "@/components/ui/card";
import { useRecentPatients } from "../hooks/usePatients";
import { calculateAge, formatDate } from "@/lib/utils";
import type { OpenMRSPatient } from "@/types/openmrs";

const PAGE_SIZE = 10;

export function PatientListTable() {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);

  const { data, isLoading, refetch } = useRecentPatients(PAGE_SIZE, page * PAGE_SIZE);
  const rows = data?.results ?? [];
  const hasMore = data?.hasMore ?? false;

  const genderLabel = (g: string) => ({ M: "Male", F: "Female", O: "Other", U: "Unknown" }[g] ?? g);
  const genderColor = (g: string) =>
    g === "M" ? "info" : g === "F" ? "warning" : "secondary";

  const goNext = () => setPage((p) => p + 1);
  const goPrev = () => setPage((p) => Math.max(0, p - 1));

  return (
    <div className="flex flex-col gap-4">
      {/* Toolbar */}
      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={() => { refetch(); }}>
          <RefreshCw size={14} className="mr-1" /> Refresh
        </Button>
        <Button size="sm" onClick={() => navigate("/patients/register")}>
          <UserPlus size={14} className="mr-1" /> Register Patient
        </Button>
      </div>

      {/* Table */}
      <Card className="overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-[var(--clinic-ice)]">
              <TableHead className="text-xs uppercase tracking-wider w-[40%]">Patient</TableHead>
              <TableHead className="text-xs uppercase tracking-wider w-[18%]">ID</TableHead>
              <TableHead className="text-xs uppercase tracking-wider w-[10%]">Age</TableHead>
              <TableHead className="text-xs uppercase tracking-wider w-[12%]">Gender</TableHead>
              <TableHead className="text-xs uppercase tracking-wider w-[12%]">DOB</TableHead>
              <TableHead className="text-xs uppercase tracking-wider w-[8%]">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading
              ? Array(PAGE_SIZE).fill(0).map((_, i) => (
                  <TableRow key={i}>
                    {Array(6).fill(0).map((_, j) => (
                      <TableCell key={j}>
                        <Skeleton className="h-4 w-full" />
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              : rows.map((patient: OpenMRSPatient) => (
                  <TableRow
                    key={patient.uuid}
                    className="hover:bg-[var(--clinic-ice)] cursor-pointer transition-colors"
                    onClick={() => navigate(`/patients/${patient.uuid}`)}
                  >
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <div className="h-8 w-8 rounded-full bg-[var(--clinic-mint)] text-[var(--clinic-blue)] flex items-center justify-center text-xs font-semibold shrink-0">
                          {patient.person.display.split(" ").map((n) => n[0]).join("").toUpperCase().slice(0, 2)}
                        </div>
                        <span className="font-medium text-[var(--clinic-ink)]">{patient.person.display}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-[var(--clinic-slate)] font-mono text-xs">
                      {patient.identifiers[0]?.identifier ?? "—"}
                    </TableCell>
                    <TableCell className="text-[var(--clinic-slate)]">
                      {calculateAge(patient.person.birthdate)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={genderColor(patient.person.gender) as "info" | "warning" | "secondary"} className="text-xs">
                        {genderLabel(patient.person.gender)}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-[var(--clinic-slate)] text-xs">
                      {formatDate(patient.person.birthdate, "short")}
                    </TableCell>
                    <TableCell>
                      <Badge variant={patient.person.dead ? "destructive" : "success"} className="text-xs">
                        {patient.person.dead ? "Deceased" : "Active"}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
            {!isLoading && rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="py-12 text-center text-[hsl(var(--muted-foreground))] text-sm">
                  No patients found.{" "}
                  <button
                    onClick={() => navigate("/patients/register")}
                    className="text-[var(--clinic-blue)] hover:underline"
                  >
                    Register the first patient
                  </button>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Card>

      {/* Pagination */}
      {!isLoading && (rows.length > 0 || page > 0) && (
        <div className="flex items-center justify-between px-1">
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Page {page + 1} · showing records {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + rows.length}
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0 || isLoading}
              onClick={goPrev}
            >
              <ChevronLeft size={14} className="mr-1" /> Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore || isLoading}
              onClick={goNext}
            >
              Next <ChevronRight size={14} className="ml-1" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
