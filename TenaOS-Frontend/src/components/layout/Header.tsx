import { Bell, LogOut, Settings, User, ChevronDown } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuthStore } from "@/stores/authStore";
import { useLogout } from "@/features/auth/useSession";
import { getInitials } from "@/lib/utils";
import { PatientSearchBar } from "@/features/patients/components/PatientSearchBar";

interface HeaderProps {
  title?: string;
}

export function Header({ title }: HeaderProps) {
  const { user } = useAuthStore();
  const logout = useLogout();
  const navigate = useNavigate();
  const handleLogout = async () => {
    await logout.mutateAsync();
    navigate("/login", { replace: true });
  };

  return (
    <header className="flex items-center h-16 gap-4 px-6 border-b border-[var(--clinic-border)] bg-white/80 backdrop-blur-sm shrink-0">
      <div className="flex items-center min-w-0 shrink-0">
        {title && (
          <h1 className="text-base font-semibold text-[var(--clinic-ink)] truncate">{title}</h1>
        )}
      </div>

      <div className="hidden md:flex flex-1 justify-center min-w-0">
        <div className="w-full max-w-xl">
          <PatientSearchBar placeholder="Search patients by name or ID..." />
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 shrink-0 ml-auto md:ml-0">
        <Button variant="ghost" size="icon" className="relative text-[var(--clinic-slate)]" aria-label="Notifications">
          <Bell size={18} />
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="flex items-center gap-2 rounded-xl px-2 py-1.5 hover:bg-[var(--clinic-ice)] transition-colors">
              <Avatar className="h-8 w-8">
                <AvatarFallback>{getInitials(user?.display ?? user?.username ?? "?")}</AvatarFallback>
              </Avatar>
              <div className="hidden sm:flex flex-col items-start">
                <span className="text-sm font-medium text-[var(--clinic-ink)] leading-tight">
                  {user?.display ?? user?.username ?? "Signed out"}
                </span>
                <span className="text-xs text-[var(--clinic-slate)] leading-tight">
                  {user?.roles[0] ?? "OpenMRS user"}
                </span>
              </div>
              <ChevronDown size={14} className="text-[var(--clinic-slate)]" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuLabel>{user?.display ?? "TenaOS"}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled>
              <User size={14} className="mr-2" /> Profile
            </DropdownMenuItem>
            <DropdownMenuItem disabled>
              <Settings size={14} className="mr-2" /> Settings
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => void handleLogout()}>
              <LogOut size={14} className="mr-2" /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
