import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileText, Wand2, Pencil, Trash2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/common/ErrorState";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { useFormList, useDeleteForm } from "../hooks/useForms";

type FormSummary = {
  uuid: string;
  name: string;
  description: string;
  version: string;
  published: boolean;
  encounterType?: { uuid: string; display: string };
};

export function FormListPage() {
  const navigate = useNavigate();
  const { data: forms, isLoading, isError, refetch } = useFormList();
  const deleteForm = useDeleteForm();
  const [pendingDelete, setPendingDelete] = useState<FormSummary | null>(null);

  const getEditPath = (form: FormSummary) => {
    const params = new URLSearchParams();
    params.set("formUuid", form.uuid);
    params.set("name", form.name);
    if (form.description) params.set("description", form.description);
    if (form.version) params.set("version", form.version);
    if (form.encounterType?.uuid) params.set("encounterTypeUuid", form.encounterType.uuid);
    return `/forms/new?${params.toString()}`;
  };

  const handleEdit = (e: React.MouseEvent, form: FormSummary) => {
    e.stopPropagation();
    navigate(getEditPath(form));
  };

  const handleDelete = (e: React.MouseEvent, form: FormSummary) => {
    e.stopPropagation();
    setPendingDelete(form);
  };

  const confirmDelete = () => {
    if (!pendingDelete) return;
    deleteForm.mutate(pendingDelete.uuid, {
      onSettled: () => setPendingDelete(null),
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">Clinical Forms</h1>
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Forms are loaded from OpenMRS and submit encounters back to OpenMRS.
          </p>
        </div>
        <Button onClick={() => navigate("/forms/new")}>
          <Wand2 size={14} className="mr-1.5" /> Create with assistant
        </Button>
      </div>

      {isError ? (
        <ErrorState title="Could not load OpenMRS forms" onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array(6).fill(0).map((_, i) => <Skeleton key={i} className="h-28 rounded-3xl" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {forms?.map((form) => (
            <Card
              key={form.uuid}
              className="h-full cursor-pointer overflow-hidden border-[var(--clinic-teal)] bg-[var(--clinic-mint)] transition-all hover:shadow-md"
              onClick={() => navigate(`/forms/${form.uuid}/fill`)}
            >
              <CardContent className="flex h-full flex-col p-4">
                {/* Top row: icon + action buttons */}
                <div className="flex items-start justify-between gap-2">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-white text-[var(--clinic-blue)] ring-2 ring-[var(--clinic-teal)]/30">
                    <FileText size={16} />
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-[var(--clinic-slate)] hover:bg-white hover:text-[var(--clinic-blue)]"
                      onClick={(e) => handleEdit(e, form)}
                      title="Edit with assistant"
                    >
                      <Pencil size={13} />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-[var(--clinic-slate)] hover:bg-red-50 hover:text-red-500"
                      onClick={(e) => handleDelete(e, form)}
                      title="Delete form"
                    >
                      <Trash2 size={13} />
                    </Button>
                  </div>
                </div>

                {/* Form name + description */}
                <h3 className="mt-3 text-sm font-semibold text-[var(--clinic-ink)]">{form.name}</h3>
                {form.description && (
                  <p className="mt-1 line-clamp-2 text-xs text-[var(--clinic-slate)]">
                    {form.description}
                  </p>
                )}

                {/* Spacer */}
                <div className="flex-1" />

                {/* Bottom: status + encounter type */}
                <div className="mt-3 flex items-center justify-between gap-3">
                  <Badge
                    variant={form.published ? "success" : "secondary"}
                    className="shrink-0 border-[var(--clinic-teal)]/30 bg-white text-xs text-[var(--clinic-blue)]"
                  >
                    {form.published ? "Published" : "Draft"}
                  </Badge>
                  {form.encounterType && (
                    <span className="truncate text-right text-xs text-[var(--clinic-slate)]">
                      {form.encounterType.display}
                    </span>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
          {forms?.length === 0 && (
            <div className="col-span-3 py-16 text-center text-[hsl(var(--muted-foreground))] text-sm">
              No OpenMRS forms are configured yet.
            </div>
          )}
        </div>
      )}

      {/* Delete confirmation */}
      <AlertDialog open={Boolean(pendingDelete)} onOpenChange={(open) => !open && setPendingDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete "{pendingDelete?.name}"?</AlertDialogTitle>
            <AlertDialogDescription>
              This retires the form in OpenMRS. Existing encounters that reference it are not affected, but the form will no longer be available for new submissions.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setPendingDelete(null)}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-500 hover:bg-red-600 text-white"
              onClick={confirmDelete}
              disabled={deleteForm.isPending}
            >
              {deleteForm.isPending ? "Deleting…" : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
